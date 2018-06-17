"""

This module contains source finding and photometry routines.

"""

import astropy.io.fits as pyfits
import numpy as np
import numpy.fft as fft
from astLib import *
import os
import pylab
import math
from scipy import ndimage
from scipy import interpolate
from nemo import catalogTools
from nemo import mapTools
from nemo import simsTools
import sys
import IPython
np.random.seed()

#------------------------------------------------------------------------------------------------------------
def findObjects(imageDict, SNMap = 'file', threshold = 3.0, minObjPix = 3, rejectBorder = 10, 
                findCenterOfMass = True, makeDS9Regions = True, writeSegmentationMap = False, 
                diagnosticsDir = None, invertMap = False, objIdent = 'ACT-CL', longNames = False, 
                verbose = True, useInterpolator = True):
    """Finds objects in the filtered maps pointed to by the imageDict. Threshold is in units of sigma 
    (as we're using S/N images to detect objects). Catalogs get added to the imageDict.
    
    SNMap == 'file' means that the SNMap will be loaded from disk, SNMap == 'array' means it is a np
    array already in memory. SNMap == 'local', means use the a ranked filter to get noise in annulus around
    given position. This doesn't seem to work as well...
    
    Throw out objects within rejectBorder pixels of edge of frame if rejectBorder != None
    
    Set invertMap == True to do a test of estimating fraction of spurious sources
    
    """
    
    if verbose == True:
        print(">>> Finding objects ...")
    
    if rejectBorder == None:
        rejectBorder=0
  
    # Load point source mask - this is hacked for 148 only at the moment
    if diagnosticsDir != None and os.path.exists(diagnosticsDir+os.path.sep+"psMask_148.fits") == True:
        img=pyfits.open(diagnosticsDir+os.path.sep+"psMask_148.fits")
        psMaskMap=img[0].data
    else:
        psMaskMap=None
                
    # Do search on each filtered map separately
    for key in imageDict['mapKeys']:
        
        if verbose == True:
            print("... searching %s ..." % (key))
        
        # Load area mask
        extName=key.split("#")[-1]
        if diagnosticsDir != None:
            img=pyfits.open(diagnosticsDir+os.path.sep+"areaMask#%s.fits" % (extName))
            areaMask=img[0].data
        else:
            areaMask=None
        
        if SNMap == 'file':
            img=pyfits.open(imageDict[key]['SNMap'])
            wcs=astWCS.WCS(imageDict[key]['SNMap'])
            data=img[0].data
        elif SNMap == 'array':
            data=imageDict[key]['SNMap']
            wcs=imageDict[key]['wcs']

        # This is for checking contamination
        if invertMap == True:
            data=data*-1
            
        # Thresholding to identify significant pixels
        sigPix=np.array(np.greater(data, threshold), dtype=int)
        sigPixMask=np.equal(sigPix, 1)
        
        # Fast, simple segmentation - don't know about deblending, but doubt that's a problem for us
        segmentationMap, numObjects=ndimage.label(sigPix)
        if writeSegmentationMap == True:
            segMapFileName=imageDict[key]['SNMap'].replace("SNMap", "segmentationMap")
            astImages.saveFITS(segMapFileName, segmentationMap, wcs)
            imageDict[key]['segmentationMap']=segMapFileName
        
        # Get object positions, number of pixels etc.
        objIDs=np.unique(segmentationMap)
        if findCenterOfMass == True:
           print("... working with center of mass method ...")
           objPositions=ndimage.center_of_mass(data, labels = segmentationMap, index = objIDs)
        else:
           print("... working with maximum position method ...")
           objPositions=ndimage.maximum_position(data, labels = segmentationMap, index = objIDs)

        objNumPix=ndimage.sum(sigPixMask, labels = segmentationMap, index = objIDs)

        # In the past this had problems - Simone using happily in his fork though, so now optional
        if useInterpolator == True:
            mapInterpolator=interpolate.RectBivariateSpline(np.arange(data.shape[0]), 
                                                            np.arange(data.shape[1]), 
                                                            data, kx = 3, ky = 3)
        else:
            print("... not using sub-pixel interpolation for SNRs ...")
                                                        
        # Border around edge where we might throw stuff out just to cut down contamination
        if np.any(areaMask) != None and areaMask.sum() > 0:
            minX=np.where(areaMask > 0)[1].min()
            maxX=np.where(areaMask > 0)[1].max()
            minY=np.where(areaMask > 0)[0].min()
            maxY=np.where(areaMask > 0)[0].max()
        else:
            minX=0
            maxX=segmentationMap.shape[1]-1
            minY=0
            maxY=segmentationMap.shape[0]-1
        minX=minX+rejectBorder
        maxX=maxX-rejectBorder
        minY=minY+rejectBorder
        maxY=maxY-rejectBorder       
        
        # Build catalog
        catalog=[]
        idNumCount=1
        for i in range(len(objIDs)):
            if type(objNumPix) != float and objNumPix[i] > minObjPix:
                objDict={}
                objDict['id']=idNumCount
                objDict['x']=objPositions[i][1]
                objDict['y']=objPositions[i][0]
                objDict['RADeg'], objDict['decDeg']=wcs.pix2wcs(objDict['x'], objDict['y'])
                if objDict['RADeg'] < 0:
                    objDict['RADeg']=360.0+objDict['RADeg']
                galLong, galLat=astCoords.convertCoords("J2000", "GALACTIC", objDict['RADeg'], objDict['decDeg'], 2000)
                objDict['galacticLatDeg']=galLat
                if longNames == False:
                    objDict['name']=catalogTools.makeACTName(objDict['RADeg'], objDict['decDeg'], prefix = objIdent)
                else:
                    objDict['name']=catalogTools.makeLongName(objDict['RADeg'], objDict['decDeg'], prefix = objIdent)                    
                objDict['numSigPix']=objNumPix[i]
                objDict['template']=key
                if useInterpolator == True:
                    objDict['SNR']=mapInterpolator(objDict['y'], objDict['x'])[0][0]
                else:
                    objDict['SNR']=data[int(round(objDict['y'])), int(round(objDict['x']))]
                if objDict['x'] > minX and objDict['x'] < maxX and \
                    objDict['y'] > minY and objDict['y'] < maxY:
                    masked=False
                    if psMaskMap != None:
                        if psMaskMap[int(round(objDict['y'])), int(round(objDict['x']))] > 0:
                            masked=True
                    if masked == False:
                        catalog.append(objDict)
                        idNumCount=idNumCount+1     
        if makeDS9Regions == True:
            catalogTools.catalog2DS9(catalog, imageDict[key]['filteredMap'].replace(".fits", ".reg"))
        imageDict[key]['catalog']=catalog

#------------------------------------------------------------------------------------------------------------
def getSNValues(imageDict, SNMap = 'file', useInterpolator = True, invertMap = False, prefix = '', 
                template = None):
    """Measures SNR values in maps at catalog positions.
    
    Set invertMap == True, to do a test for estimating the spurious source fraction
    
    Use prefix to add prefix before SNR in catalog (e.g., fixed_SNR)
    
    Use template to select a particular filtered map (i.e., label of mapFilter given in .par file, e.g.,
    a filter used to measure properties for all objects at fixed scale).
    
    """
            
    print(">>> Getting %sSNR values ..." % (prefix))
    
    # Do search on each filtered map separately
    for key in imageDict['mapKeys']:
        
        print("... searching %s ..." % (key))
        
        if template == None:
            templateKey=key
        else:
            templateKey=None
            for k in imageDict['mapKeys']:
                # Match the reference filter name and the extName
                if k.split("#")[0] == template and k.split("#")[-1] == key.split("#")[-1]:
                    templateKey=k
            if templateKey == None:
                raise Exception("didn't find templateKey")
        
        if SNMap == 'file':
            img=pyfits.open(imageDict[templateKey]['SNMap'])
            wcs=astWCS.WCS(imageDict[templateKey]['SNMap'])
            data=img[0].data
        elif SNMap == 'array':
            data=imageDict[templateKey]['SNMap']
            wcs=imageDict[templateKey]['wcs']
        else:
            raise Exception("Didn't understand SNMap value '%s'" % (str(SNMap)))

        # This is for checking contamination
        if invertMap == True:
            data=data*-1
            
        # In the past this had problems - Simone using happily in his fork though, so now optional
        if useInterpolator == True:
            mapInterpolator=interpolate.RectBivariateSpline(np.arange(data.shape[0]), 
                                                            np.arange(data.shape[1]), 
                                                            data, kx = 3, ky = 3)
        else:
            print("... not using sub-pixel interpolation for SNRs ...")
                                            
        for obj in imageDict[key]['catalog']:
            x, y=wcs.wcs2pix(obj['RADeg'], obj['decDeg'])
            y=int(round(obj['y']))
            x=int(round(obj['x']))
            if x > 0 and x < data.shape[1] and y > 0 and y < data.shape[0]:
                if useInterpolator == True:
                    obj[prefix+'SNR']=mapInterpolator(obj['y'], obj['x'])[0][0]
                else:
                    obj[prefix+'SNR']=data[y, x] # read directly off of S/N map
            else:
                obj[prefix+'SNR']=0.
       
#------------------------------------------------------------------------------------------------------------
def measureFluxes(imageDict, photometryOptions, diagnosticsDir, useInterpolator = True, unfilteredMapsDict = None):
    """Add flux measurements to each catalog pointed to in the imageDict. Measured in 'outputUnits' 
    specified in the filter definition in the .par file (and written to the image header as 'BUNIT').
    
    Maps should have been normalised before this stage to make the value of the filtered map at the object
    position equal to the flux that is wanted (yc, uK or Jy/beam). This includes taking out the effect of
    the beam.
    
    Under 'photometryOptions', use the 'photFilter' key to choose which filtered map in which to make
    flux measurements at fixed scale (fixed_delta_T_c, fixed_y_c etc.) 

    """
    
    print(">>> Doing photometry ...")

    # For fixed filter scale
    if 'photFilter' in list(photometryOptions.keys()):
        photFilter=photometryOptions['photFilter']
    else:
        photFilter=None

    # Adds fixed_SNR values to catalogs for all maps
    if photFilter != None:
        getSNValues(imageDict, SNMap = 'file', prefix = 'fixed_', template = photFilter, 
                    useInterpolator = useInterpolator)

    for key in imageDict['mapKeys']:
        
        print("--> Map: %s ..." % (imageDict[key]['filteredMap']))
        catalog=imageDict[key]['catalog']        
        
        img=pyfits.open(imageDict[key]['filteredMap'])
        wcs=astWCS.WCS(imageDict[key]['filteredMap'])
        mapData=img[0].data

        mapUnits=wcs.header['BUNIT']                # could be 'yc' or 'Jy/beam'
        
        # In the past this had problems - Simone using happily in his fork though, so now optional
        if useInterpolator == True:
            mapInterpolator=interpolate.RectBivariateSpline(np.arange(mapData.shape[0]), 
                                                            np.arange(mapData.shape[1]), 
                                                            mapData, kx = 3, ky = 3)
        else:
            print("... not using sub-pixel interpolation for fluxes ...")
            mapInterpolator=None
        
        # Add fixed filter scale maps
        mapDataList=[mapData]
        interpolatorList=[mapInterpolator]
        prefixList=['']
        extName=key.split("#")[-1]
        if 'photFilter' in list(photometryOptions.keys()):
            photImg=pyfits.open(imageDict[photFilter+"#"+extName]['filteredMap'])
            photMapData=photImg[0].data
            mapDataList.append(photMapData)
            prefixList.append('fixed_')
            if useInterpolator == True:
                photMapInterpolator=interpolate.RectBivariateSpline(np.arange(photMapData.shape[0]), 
                                                                    np.arange(photMapData.shape[1]), 
                                                                    photMapData, kx = 3, ky = 3)
            else:
                photMapInterpolator=None
            interpolatorList.append(photMapInterpolator)
            
        for obj in catalog:
                        
            if key == obj['template']:
                
                for data, prefix, interpolator in zip(mapDataList, prefixList, interpolatorList):
                    # NOTE: We might want to avoid 2d interpolation here because that was found not to be robust elsewhere
                    # 2018: Simone seems to now be using this happily, so now optional
                    if useInterpolator == True:
                        mapValue=interpolator(obj['y'], obj['x'])[0][0]
                    else:
                        mapValue=data[int(round(obj['y'])), int(round(obj['x']))]
                    # NOTE: remember, all normalisation should be done when constructing the filtered maps, i.e., not here!
                    if mapUnits == 'yc':
                        yc=mapValue
                        deltaTc=mapTools.convertToDeltaT(yc, obsFrequencyGHz = 148.0)
                        obj[prefix+'y_c']=yc/1e-4                            # So that same units as H13 in output catalogs
                        obj[prefix+'err_y_c']=obj[prefix+'y_c']/obj[prefix+'SNR']
                        obj[prefix+'deltaT_c']=deltaTc
                        obj[prefix+'err_deltaT_c']=abs(deltaTc/obj[prefix+'SNR'])
                    elif mapUnits == 'Y500':
                        print("add photometry.measureFluxes() for Y500")
                        IPython.embed()
                        sys.exit()
                    elif mapUnits == 'uK':
                        # For this, we want deltaTc to be source amplitude
                        deltaTc=mapValue
                        obj[prefix+'deltaT_c']=deltaTc
                        obj[prefix+'err_deltaT_c']=deltaTc/obj[prefix+'SNR']
                    elif mapUnits == 'Jy/beam':
                        # For this, we want mapValue to be source amplitude == flux density
                        print("add photometry.measureFluxes() Jy/beam photometry")
                        IPython.embed()
                        sys.exit()
                
#------------------------------------------------------------------------------------------------------------
def getRadialDistanceMap(objDict, data, wcs):
    """Returns an array of same dimensions as data but containing radial distance to the object specified
    in the objDict in units degrees on the sky
    
    """
    
    x0=objDict['x']
    y0=objDict['y']
    x1=x0+1
    y1=y0+1
    ra1, dec1=wcs.pix2wcs(x1, y1)
    xPixScale=astCoords.calcAngSepDeg(objDict['RADeg'], objDict['decDeg'], ra1, objDict['decDeg'])
    yPixScale=astCoords.calcAngSepDeg(objDict['RADeg'], objDict['decDeg'], objDict['RADeg'], dec1)       
    xRange=np.array([np.arange(0, data.shape[1])-x0]*data.shape[0])*xPixScale
    yRange=(np.array([np.arange(0, data.shape[0])-y0]*data.shape[1])*yPixScale).transpose()
    rRange=np.sqrt(xRange**2+yRange**2)       

    return rRange

#------------------------------------------------------------------------------------------------------------
def getPixelsDistanceMap(objDict, data):
    """Returns an array of same dimensions as data but containing radial distance to the object specified
    in the objDict in units of pixels
    
    """
    
    x0=objDict['x']
    y0=objDict['y']
    x1=x0+1
    y1=y0+1     
    xRange=np.array([np.arange(0, data.shape[1])-x0]*data.shape[0])
    yRange=(np.array([np.arange(0, data.shape[0])-y0]*data.shape[1])).transpose()
    rRange=np.sqrt(xRange**2+yRange**2)       

    return rRange

#------------------------------------------------------------------------------------------------------------
def makeAnnulus(innerScalePix, outerScalePix):
    """Makes the footprint of an annulus for feeding into the rank filter
    
    Returns annulus, numValues
    
    """

    # Make the footprint for the rank filter
    #bckInnerScalePix=beamSigmaPix*3#int(round((bckInnerRadiusArcmin/60.0)/yPixScale))
    #bckOuterScalePix=beamSigmaPix*5#int(round((bckOuterRadiusArcmin/60.0)/yPixScale))
    bckInnerScalePix=int(round(innerScalePix))
    bckOuterScalePix=int(round(outerScalePix))
    annulus=np.zeros([bckOuterScalePix*2, bckOuterScalePix*2])
    xRange=np.array([np.arange(annulus.shape[1])-bckOuterScalePix]*annulus.shape[0])
    yRange=np.array([np.arange(annulus.shape[0])-bckOuterScalePix]*annulus.shape[1]).transpose()
    rRange=np.sqrt(xRange**2+yRange**2)
    annulus[np.logical_and(np.greater(rRange, bckInnerScalePix),
                                np.less(rRange, bckOuterScalePix))]=1.0
    #numValues=annulus.flatten().nonzero()[0].shape[0]
    
    return annulus.astype('int64')

