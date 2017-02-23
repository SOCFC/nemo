# -*- coding: utf-8 -*-
"""This module contains routines for comparing measured fluxes to input sims.

"""

import pyfits
from astLib import *
from scipy import ndimage
from scipy import interpolate
from scipy import stats
import time
import astropy.table as atpy
import mapTools
import catalogTools
import photometry
import numpy
import os
import math
import pylab
import pickle
import sys
import operator
import pyximport; pyximport.install()
import nemoCython
import IPython
numpy.random.seed()

#------------------------------------------------------------------------------------------------------------
def parseInputSimCatalog(fileName, wcs):
    """Parses input simulation catalog (i.e. as produced by Hy and friends, well actually they look like 
    they've been fiddled a bit by Ryan), returns dictionary list. Only returns objects that are found within
    the map boundaries defined by the wcs.
        
    """
    
    inFile=file(fileName, "r")
    lines=inFile.readlines()
    inFile.close()
    
    catalog=[]
    idNum=0
    for line in lines:
        if len(line) > 3 and line[0] != "#":
            objDict={}
            bits=line.split()
            idNum=idNum+1
            objDict['id']=idNum
            objDict['z']=float(bits[2])
            objDict['RADeg']=float(bits[0])
            objDict['decDeg']=float(bits[1])
            objDict['name']=catalogTools.makeACTName(objDict['RADeg'], objDict['decDeg'])
            objDict['Mvir']=float(bits[3]) 
            objDict['RvirMpc']=float(bits[4])
            objDict['fluxFromCatalog_arcmin2']=float(bits[5])
            if wcs.coordsAreInImage(objDict['RADeg'], objDict['decDeg']) == True:
                catalog.append(objDict)
    
    # Check this works okay
    catalogTools.catalog2DS9(catalog, 'inputSimObjects.reg')
    
    return catalog

#------------------------------------------------------------------------------------------------------------
def matchAgainstSimCatalog(catalog, inputSimCatalog, simFluxKey = 'fixedApertureFluxFromInputSimMap_arcmin2'):
    """Matches the given catalog against the given input sim catalog, adding the input sim flux, mass etc.
    info for all matched objects to the catalog. Needed for feeding the catalog into routines that estimate
    completeness, purity.
    
    """
    
    # For faster cross matching
    sRAs=[]
    sDecs=[]
    for s in inputSimCatalog:
        sRAs.append(s['RADeg'])
        sDecs.append(s['decDeg'])
    sRAs=numpy.array(sRAs)
    sDecs=numpy.array(sDecs)
    
    for m in catalog:
        mra=m['RADeg']
        mdec=m['decDeg']
        rMin=1e6
        bestMatch=None
                        
        # Faster matching - best matches here by definition only actually had sim fluxes measured
        # if they passed the Y, z limit cuts. simCatalog still contains every object in map area.
        rs=astCoords.calcAngSepDeg(mra, mdec, sRAs, sDecs)
        rMin=rs.min()
        rMinIndex=numpy.equal(rs, rMin).nonzero()[0][0]
        bestMatch=inputSimCatalog[rMinIndex]
        if bestMatch != None and rMin < catalogTools.XMATCH_RADIUS_DEG:
            # We want to track all matches we detected even if we don't necessarily want to compare flux
            m['inputSim_Mvir']=bestMatch['Mvir']
            m['inputSim_z']=bestMatch['z']
            m['inputSim_RvirMpc']=bestMatch['RvirMpc']
            if simFluxKey in bestMatch.keys():
                m['inputSim_flux_arcmin2']=bestMatch[simFluxKey]
            else:
                m['inputSim_flux_arcmin2']=None
        else:
            m['inputSim_flux_arcmin2']=None
            m['inputSim_z']=None
            m['inputSim_RvirMpc']=None
            m['inputSim_Mvir']=None
               
#------------------------------------------------------------------------------------------------------------
def compareToInputSimCatalog(imageDict, inputSimCatFileName, inputSimMapFileName, photometryOptions, 
                             outDir = ".", YLimit = None, zRange = None, convertFromJySrToMicroK = False,
                             obsFreqGHz = 148, clusterProfilesToPlot = []):
    """This is a wrapper for calling all the stuff we might want to check against the
    input sims, e.g. purity, completeness etc. Saves out results under outDir.
    
    Can use clusterProfilesToPlot to list names of objects to make plots of e.g. cumulative flux vs. radius
    for each filtered map.
    
    """
    
    print ">>> Checking fluxes against input sim ..."        
    if os.path.exists(outDir) == False:
        os.makedirs(outDir)
    
    signalTemplatesList=[]
    for k in imageDict.keys():
        if k != "mergedCatalog" and k != "optimalCatalog":
            signalTemplatesList.append(k)
    
    # Need a data map WCS for working out which objects in the sim catalog are in our data map
    dataMapWCS=astWCS.WCS(imageDict[signalTemplatesList[0]]['ycFilteredMap'])
    
    # Get sim catalog
    simCatalog=getInputSimApertureFluxes(inputSimCatFileName, inputSimMapFileName, dataMapWCS, \
                                         apertureRadiusArcmin = photometryOptions["apertureRadiusArcmin"], \
                                         YLimit = YLimit, zRange = zRange, \
                                         convertFromJySrToMicroK = convertFromJySrToMicroK, obsFreqGHz = obsFreqGHz)
    simFluxKey='fixedApertureFluxFromInputSimMap_arcmin2'

    # Checking fluxes in maps/catalog agree - they do, so this should no longer be necessary
    #simCatalog=checkInputSimCatalogFluxes(inputSimCatFileName, inputSimMapFileName, YLimit = YLimit, \
                            #zRange = zRange, convertFromJySrToMicroK = convertFromJySrToMicroK, \
                            #obsFreqGHz = obsFreqGHz)
    
    # We will want to make plots of optimal template, plus see individual tenplate results at the same time
    # For both the flux comparison and completeness/purity plots
    catalogsDict={}
    catalogsDict['optimal']=imageDict['optimalCatalog']
    for st in signalTemplatesList:
        catalogsDict[st]=imageDict[st]['catalog']
    
    # Match all catalogs against input sim
    matchAgainstSimCatalog(imageDict['optimalCatalog'], simCatalog)
    for key in catalogsDict.keys():
        matchAgainstSimCatalog(catalogsDict[key], simCatalog)

    # Flux comparison plot
    fig=pylab.figure(num=4, figsize=(10,8))
    fig.canvas.set_window_title('Measured vs. Input Flux Comparison')
    topPlot=pylab.axes([0.12, 0.35, 0.8, 0.6])
    bottomPlot=pylab.axes([0.12, 0.1, 0.8, 0.23])
    keysList=catalogsDict.keys()
    keysList.reverse()  # so optimal gets plotted first
    for key in keysList:
        catalog=catalogsDict[key]
        names=[]
        simFlux=[]
        myFlux=[]
        myFluxErr=[]
        for obj in catalog:
            if 'inputSim_flux_arcmin2' in obj.keys() and obj['inputSim_flux_arcmin2'] != None:
                names.append(obj['name'])
                myFlux.append(obj['flux_arcmin2'])
                myFluxErr.append(obj['fluxErr_arcmin2'])
                simFlux.append(obj['inputSim_flux_arcmin2'])
        simFlux=numpy.array(simFlux)
        myFlux=numpy.array(myFlux)
        myFluxErr=numpy.array(myFluxErr)
        if len(simFlux) > 0:
            if key == 'optimal':
                pylab.axes(topPlot)
                pylab.errorbar(simFlux, myFlux, yerr=myFluxErr, fmt='ko', label = 'Optimal S/N template')
                pylab.axes(bottomPlot)
                pylab.errorbar(simFlux, simFlux-myFlux, yerr=myFluxErr, fmt='ko', label = 'Optimal S/N template')
            else:
                pylab.axes(topPlot)
                pylab.errorbar(simFlux, myFlux, yerr=myFluxErr, fmt='.', label = key)
                pylab.axes(bottomPlot)
                pylab.errorbar(simFlux, simFlux-myFlux, yerr=myFluxErr, fmt='.', label = key)

    pylab.axes(bottomPlot)
    plotRange=numpy.linspace(0, 0.02, 10)
    pylab.plot(plotRange, [0]*len(plotRange), 'k--')
    pylab.ylim(-0.0015, 0.0015)
    pylab.xlim(0.0, 0.0015)
    pylab.ylabel('$\Delta$Y (arcmin$^2$)')
    pylab.xlabel('Input Sim Catalog Y (arcmin$^2$)')
    
    pylab.axes(topPlot)
    plotRange=numpy.linspace(0, 0.02, 10)
    pylab.plot(plotRange, plotRange, 'k--')
    pylab.ylabel('Measured Y (arcmin$^2$)')
    pylab.xticks([], [])
    pylab.xticks(bottomPlot.get_xticks())
    pylab.xlim(0.0, 0.0015)
    pylab.ylim(0.0, 0.0015)
    #legAxes=pylab.axes([0.65, 0.3, 0.2, 0.4], frameon=False)
    #pylab.xticks([], [])
    #pylab.yticks([], [])
    #pylab.legend(loc="center")
    pylab.legend(loc="best", prop=pylab.matplotlib.font_manager.FontProperties(family='sans-serif', size=10))
    pylab.savefig(outDir+os.path.sep+"catalogFluxesVsMeasuredFluxes.png")

    #print "In flux recovery bit"
    #ipshell()
    #sys.exit()
    
    # Simple flux recovery stats
    # These are in units of 1-sigma error bars
    # So sigmaResidualSigma = 3 would mean that the standard deviation of my fluxes is 3 error bars
    # and medianResidualSigma = 0.3 means that we're reasonably unbiased (within 0.3 error bars)
    medianResidualSigma=numpy.median((simFlux-myFlux)/myFluxErr)
    sigmaResidualSigma=numpy.std(((simFlux-myFlux)/myFluxErr))
    print ">>> Flux recovery stats:"
    print "... median residual = %.3f error bars" % (medianResidualSigma)
    print "... stdev residual = %.3f error bars" % (sigmaResidualSigma)
    
    # Now we want to do completeness and purity as function of S/N and mass
    fig=pylab.figure(num=5, figsize=(10,8))
    fig.canvas.set_window_title('Completeness and Purity')
    pylab.subplots_adjust(left=0.1, bottom=0.07, right=0.95, top=0.95, wspace=0.02, hspace=0.27)
    compAxes=pylab.subplot(311)
    trueDetAxes=pylab.subplot(312)
    purityAxes=pylab.subplot(313)
    keysList.reverse()   # in this case, we want optimal plotted last

    # Completeness
    simMasses=[]
    for obj in simCatalog:
        simMasses.append(obj['Mvir'])  
    simMasses=numpy.array(simMasses)
    simMasses.sort()
    simNum=numpy.ones(simMasses.shape[0])
    
    for key in keysList:
        catalog=catalogsDict[key]
        
        # Completeness
        detected=numpy.zeros(simMasses.shape[0])
        for obj in catalog:
            if obj['inputSim_Mvir'] != None:
                index=numpy.equal(simMasses, obj['inputSim_Mvir']).nonzero()[0][0]
                detected[index]=detected[index]+1    
        cumTotal=1+simNum.sum()-simNum.cumsum()
        cumDetected=1+detected.sum()-detected.cumsum()
        pylab.axes(compAxes)
        if key == 'optimal':
            pylab.plot(simMasses/1e14, cumDetected/cumTotal, 'k', lw=2, label = 'Optimal S/N template')
        else:
            pylab.plot(simMasses/1e14, cumDetected/cumTotal, '--', label = key)
        
        # True detections
        realObjs=[]
        for obj in catalog:
            if obj['inputSim_Mvir'] != None:
                realObjs.append([obj['SNR'], 1])
            else:
                realObjs.append([obj['SNR'], 0])
        realObjs=sorted(realObjs, key=operator.itemgetter(0))
        realObjs=numpy.array(realObjs).transpose()
        pylab.axes(trueDetAxes)
        if key == 'optimal':
            # yes, +1 is essential
            pylab.plot(realObjs[0], 1+realObjs[1].sum()-realObjs[1].cumsum(), 'k', lw=2, label = 'Optimal S/N template')
        else:
            pylab.plot(realObjs[0], 1+realObjs[1].sum()-realObjs[1].cumsum(), '--', label = key)

        # Purity
        purity=(1+realObjs[1].sum()-realObjs[1].cumsum())/ \
               (realObjs.shape[1]-numpy.arange(realObjs[1].shape[0], dtype=float))
        pylab.axes(purityAxes)
        if key == 'optimal':
            pylab.plot(realObjs[0], purity, 'k', lw=2, label = 'Optimal S/N template')
        else:
            pylab.plot(realObjs[0], purity, '--', label = key)
            
    # Completeness
    pylab.axes(compAxes)
    pylab.xlabel("Mass ($\\times 10^{14}$ M$_\odot$)")
    pylab.ylabel("Completeness > Mass")
    pylab.ylim(0, 1.1)
    
    # True detections
    pylab.axes(trueDetAxes)
    pylab.xlabel("S/N")
    pylab.ylabel("True Detections > S/N")
    pylab.legend(loc="upper right", prop=pylab.matplotlib.font_manager.FontProperties(family='sans-serif', size=10))
    
    # Purity
    pylab.axes(purityAxes)
    pylab.xlabel("S/N")
    pylab.ylabel("Purity > S/N")
    pylab.ylim(0, 1.1)
    
    pylab.savefig(outDir+os.path.sep+"completenessAndPurity.png")
        
#------------------------------------------------------------------------------------------------------------
def getInputSimApertureFluxes(inputSimCatFileName, inputSimMapFileName, dataMapWCS, 
                              apertureRadiusArcmin = 3.0, YLimit = None, zRange = None, 
                              convertFromJySrToMicroK = False, obsFreqGHz = 148, saveAsY = True):
    """This parses the input sim catalog file, adding every object in it that falls within the map
    to a list of objects. For objects that additionally pass the given Y, z cuts, their fluxes
    are measured directly from the input sim map through the specified circular aperture.
    
    dataMapWCS is needed to work out which input sim catalog objects are actually in the map. Speeds things
    up!
    
    Note that YLimit, zLimit are on quantities in the input catalog, which are measured within virial radius.
    
    """
    
    # We may want these again, so pickle the results
    if os.path.exists("nemoCache") == False:
        os.makedirs("nemoCache")
        
    pickleFileName="nemoCache/inputSimFluxes.pickled"
    if os.path.exists(pickleFileName) == True:
        print ">>> Loading previously measured input sim map fluxes ..."
        pickleFile=file(pickleFileName, "r")
        unpickler=pickle.Unpickler(pickleFile)
        inputCatalog=unpickler.load()
    else:
        
        print ">>> Loading noiseless input sim catalog and getting fluxes from input sim map  ... "
        
        inputCatalog=parseInputSimCatalog(inputSimCatFileName, dataMapWCS)

        # Measure fluxes in actual input map from directly adding up flux within given aperture           
        img=pyfits.open(inputSimMapFileName)
        wcs=astWCS.WCS(inputSimMapFileName)
        data=img[0].data
        if convertFromJySrToMicroK == True: # from Jy/sr
            if obsFreqGHz == 148:
                print ">>> Converting from Jy/sr to micro kelvins assuming obsFreqGHz = %.0f" % (obsFreqGHz)
                data=(data/1.072480e+09)*2.726*1e6
            else:
                raise Exception, "no code added to support conversion to uK from Jy/sr for freq = %.0f GHz" % (obsFreqGHz)
        data=mapTools.convertToY(data, obsFrequencyGHz = obsFreqGHz)
        if saveAsY == True:
            astImages.saveFITS("yc_inputSimMap.fits", data, wcs)
            
        count=0
        for obj in inputCatalog:            
            # Progress report
            count=count+1
            tenPercent=len(inputCatalog)/10
            for j in range(0,11):
                if count == j*tenPercent:
                    print "... "+str(j*10)+"% complete ..."
            # Only measure flux if this object passes Y, z limit cuts (otherwise this will take forever!)
            if obj['z'] > zRange[0] and obj['z'] < zRange[1] and obj['fluxFromCatalog_arcmin2'] > YLimit:
                x0, y0=wcs.wcs2pix(obj['RADeg'], obj['decDeg'])
                ra0, dec0=[obj['RADeg'], obj['decDeg']]
                ra1, dec1=wcs.pix2wcs(x0+1, y0+1)
                xLocalDegPerPix=astCoords.calcAngSepDeg(ra0, dec0, ra1, dec0)
                yLocalDegPerPix=astCoords.calcAngSepDeg(ra0, dec0, ra0, dec1)
                localDegPerPix=astCoords.calcAngSepDeg(obj['RADeg'], obj['decDeg'], ra1, dec1) 
                RvirDeg=math.degrees(math.atan(obj['RvirMpc']/astCalc.da(obj['z'])))
                RvirPix=RvirDeg/localDegPerPix                
                flux_arcmin2=photometry.objectFluxInAperture(obj, apertureRadiusArcmin, data, wcs)
                obj['fixedApertureFluxFromInputSimMap_arcmin2']=flux_arcmin2
            
        # Pickle results for speed
        pickleFile=file(pickleFileName, "w")
        pickler=pickle.Pickler(pickleFile)
        pickler.dump(inputCatalog)
                    
    return inputCatalog    
    
#------------------------------------------------------------------------------------------------------------
def checkInputSimCatalogFluxes(inputSimCatFileName, inputSimMapFileName, YLimit = None, zRange = None,
                               outDir = ".", convertFromJySrToMicroK = False, obsFreqGHz = 148):
    """This measures fluxes directly from the input sim map, and compares to catalog, doing a least squares 
    fit. Saves out plot under 'inputSimChecks'.
        
    """

    print ">>> Getting fluxes from noiseless input sim catalog ... "
    
    inputCatalog=parseInputSimCatalog(inputSimCatFileName, YLimit = YLimit, zRange = zRange)

    # Handy DS9 .reg file
    catalog2DS9(inputCatalog, outDir+os.path.sep+"inputSimCatalog.reg", idKeyToUse = 'name', \
                    color = "yellow")

    # Measure fluxes in actual input map from directly adding up flux within given Rvir    
    img=pyfits.open(inputSimMapFileName)
    wcs=astWCS.WCS(inputSimMapFileName)
    data=img[0].data
    if convertFromJySrToMicroK == True: # from Jy/sr
        if obsFreqGHz == 148:
            print ">>> Converting from Jy/sr to micro kelvins assuming obsFreqGHz = %.0f" % (obsFreqGHz)
            data=(data/1.072480e+09)*2.726*1e6
        else:
            raise Exception, "no code added to support conversion to uK from Jy/sr for freq = %.0f GHz" % (obsFreqGHz)
    data=mapTools.convertToY(data, obsFrequencyGHz = obsFreqGHz)

    for obj in inputCatalog:
        
        x0, y0=wcs.wcs2pix(obj['RADeg'], obj['decDeg'])
        ra0, dec0=[obj['RADeg'], obj['decDeg']]
        ra1, dec1=wcs.pix2wcs(x0+1, y0+1)
        xLocalDegPerPix=astCoords.calcAngSepDeg(ra0, dec0, ra1, dec0)
        yLocalDegPerPix=astCoords.calcAngSepDeg(ra0, dec0, ra0, dec1)
        localDegPerPix=astCoords.calcAngSepDeg(obj['RADeg'], obj['decDeg'], ra1, dec1)   
        arcmin2PerPix=xLocalDegPerPix*yLocalDegPerPix*60.0**2
        RvirDeg=math.degrees(math.atan(obj['RvirMpc']/astCalc.da(obj['z'])))
        RvirPix=RvirDeg/localDegPerPix
        clip=astImages.clipImageSectionPix(data, x0, y0, int(round(RvirPix*3)))
        
        # This automatically chucks out objects too close to edge of map
        if clip.shape[1] == clip.shape[0]:
            obj['RvirPix']=RvirPix
            obj['RvirArcmin']=RvirDeg*60.0
            x=numpy.arange(-clip.shape[1]/2, clip.shape[1]/2, dtype=float)*localDegPerPix
            y=numpy.arange(-clip.shape[0]/2, clip.shape[0]/2, dtype=float)*localDegPerPix
            rDeg=numpy.sqrt(x**2+y**2)            
            insideApertureMask=numpy.less(rDeg, RvirDeg)
            obj['fluxFromInputSimMap_arcmin2']=clip[insideApertureMask].sum()*arcmin2PerPix
    
    # Fit for not understood offset between directly measured input map fluxes and the catalog
    fluxesFromMap=[]
    fluxesFromCatalog=[]
    for obj in inputCatalog:
        if 'fluxFromInputSimMap_arcmin2' in obj.keys():
            fluxesFromMap.append(obj['fluxFromInputSimMap_arcmin2'])
            fluxesFromCatalog.append(obj['fluxFromCatalog_arcmin2'])
    fluxesFromMap=numpy.array(fluxesFromMap)
    fluxesFromCatalog=numpy.array(fluxesFromCatalog)
    
    # Clipped OLS fit
    res=numpy.zeros(fluxesFromMap.shape)
    sigma=1e6
    for i in range(10):
        fitData=[]
        for m, c, r in zip(fluxesFromMap, fluxesFromCatalog, res):
            if abs(r) < 2.0*sigma:
                fitData.append([c, m])
        fit=astStats.OLSFit(fitData)
        res=(fluxesFromCatalog*fit['slope']+fit['intercept'])-fluxesFromMap
        sigma=numpy.std(res)

    # Plot    
    fig=pylab.figure(num=6, figsize=(8,8))
    fig.canvas.set_window_title('Noiseless Input Sim Catalog vs. Map Flux Comparison')
    fitRange=numpy.arange(0, 0.02, 0.001)
    fitLine=fit['slope']*fitRange+fit['intercept']
    pylab.plot(fluxesFromCatalog, fluxesFromMap, 'ro')
    pylab.plot(fitRange, fitLine, 'b--', label='fit = %.6f*x + %.6f' % (fit['slope'], fit['intercept']))
    #pylab.plot(numpy.arange(0, 0.02, 0.001), numpy.arange(0, 0.02, 0.001), 'b--')
    pylab.xlabel("Y input sim catalog (arcmin$^2$)")
    pylab.ylabel("Y input sim map (arcmin$^2$)")
    pylab.xlim(0, 0.02)
    pylab.ylim(0, 0.02)
    pylab.legend()
    pylab.savefig(outDir+os.path.sep+"noiselessInputSim_catalogVsMapFluxes.png")
        
    # Put corrected fluxes into our input catalog
    for obj in inputCatalog:
        if 'fluxFromInputSimMap_arcmin2' in obj.keys():
            obj['correctedFlux_arcmin2']=fit['slope']*obj['fluxFromCatalog_arcmin2']+fit['intercept']

    return inputCatalog

#-------------------------------------------------------------------------------------------------------------
def fakeSourceSims(fakeSourceSimOptions, unfilteredMapsDictList, filtersList, detectionThresholdSigma, 
                   detectionMinObjPix, rejectBorderPix, minSNToInclude, photometryOptions, diagnosticsDir):
    """For each of the unfilteredMaps, inserts fake clusters with a range of different Ys, sizes etc.. and
    runs the source finding and photometry over them. Makes plots of the completeness as a function of Y, 
    size and plots of flux recovery (input fake source flux vs. recovered flux). All output is stored in
    a subdir under diagnosticsDir/
    
    """
    
    print ">>> Running completeness & flux recovery sims ..."
    
    outDir=diagnosticsDir+os.path.sep+"fakeSourceSims"
    if os.path.exists(outDir) == False:
        os.makedirs(outDir)
    
    # Make a bunch of fake maps to filter
    # We'll randomly populate each fake map with sources drawn from the list of allowed scales, deltaTs
    # rather than doing a separate run with each set of source parameters
    numRuns=fakeSourceSimOptions['numRuns']
    sourcesPerRun=fakeSourceSimOptions['sourcesPerRun']
    scalesArcminList=fakeSourceSimOptions['scalesArcminList']
    deltaTList=fakeSourceSimOptions['deltaTList']
    
    # Selection function stuff - how this works:
    #
    # selFnDict -> profileType keys -> propertiesToTrack keys -> recovered, total per run keys
    #
    # e.g. selFnDict -> 'betaProfile' -> 'deltaT'
    #                                 -> 'scaleArcmin'
    selFnDict={}
    propertiesToTrack=['deltaT', 'scaleArcmin']
    propertyPlotLabels=['$\Delta T_c$ ($\mu$K)', '$\\theta_c$ (arcmin)']
    propertyValuesLists=[deltaTList, scalesArcminList]
    
    for profileType in fakeSourceSimOptions['profilesList']:
        
        if profileType == 'betaModel':
            insertSourceIntoMap=insertBetaModelIntoMap
        elif profileType == 'arnaudModel':
            insertSourceIntoMap=insertArnaudModelIntoMap
        elif profileType == 'projectedNFWModel':
            insertSourceIntoMap=insertProjectedNFWModelIntoMap
        else:
            raise Exception, "didn't understand profileType"
        
        # Set up selection function storage
        if profileType not in selFnDict.keys():
            selFnDict[profileType]={}
            for prop, valuesList in zip(propertiesToTrack, propertyValuesLists):
                selFnDict[profileType][prop]={'recoveredByRun': numpy.zeros([numRuns, len(valuesList)], dtype=float), 
                                              'totalByRun': numpy.zeros([numRuns, len(valuesList)], dtype=float),
                                              'valuesList': valuesList}        
            
        inputYArcmin2=[]
        recoveredYArcmin2=[]
        inputDeltaTc=[]
        recoveredDeltaTc=[]
        for run in range(numRuns):
            
            t0=time.time()
            print "--> Run: %d" % (run+1)
            
            label='%s_run_%d' % (profileType, run+1)
            outFileName=outDir+os.path.sep+"fakeMap_%s.fits" % (label)
            
            # Load in map, do minimal pre-processing (trimming etc.). Assuming here 148 GHz only
            fakeMapDict={}
            fakeMapDict['RADecSection']=unfilteredMapsDictList[0]['RADecSection']
            fakeMapDict['obsFreqGHz']=unfilteredMapsDictList[0]['obsFreqGHz']
            fakeMapDict['units']=unfilteredMapsDictList[0]['units']
            fakeMapDict['beamFWHMArcmin']=unfilteredMapsDictList[0]['beamFWHMArcmin']
            fakeMapDict['pointSourceRemoval']=None
            fakeMapDict['mapFileName']=unfilteredMapsDictList[0]['mapFileName']
            fakeMapDict['weightsFileName']=unfilteredMapsDictList[0]['weightsFileName']
            mapTools.preprocessMapDict(fakeMapDict, diagnosticsDir = diagnosticsDir)
            
            # Stops cython complaining
            fakeMapDict['data']=numpy.array(fakeMapDict['data'], dtype=numpy.float64) 
            
            # Uncomment out below if want to do noiseless sanity checks on e.g. recovered Y etc.
            fakeMapDict['data']=numpy.zeros(fakeMapDict['data'].shape, dtype=numpy.float64) 
             
            # Generate fake source catalog
            fakeInputCatalog=[]
            xMin=0+rejectBorderPix*2
            xMax=fakeMapDict['data'].shape[1]-rejectBorderPix*2
            yMin=0+rejectBorderPix*2
            yMax=fakeMapDict['data'].shape[0]-rejectBorderPix*2
            xs=numpy.random.randint(xMin, xMax, sourcesPerRun)
            ys=numpy.random.randint(yMin, yMax, sourcesPerRun)
            wcsCoords=fakeMapDict['wcs'].pix2wcs(xs, ys)
            wcsCoords=numpy.array(wcsCoords)
            RAs=wcsCoords[:, 0]
            decs=wcsCoords[:, 1]
            for k in range(sourcesPerRun):
                objDict={}
                objDict['name']="fake_"+catalogTools.makeACTName(RAs[k], decs[k]).replace(" ", "_")
                objDict['RADeg']=RAs[k]
                objDict['decDeg']=decs[k]
                objDict['x']=xs[k]
                objDict['y']=ys[k]
                fakeInputCatalog.append(objDict)
            
            # Insert fake catalog sources into map, add source properties to catalog (integrated Y etc.)
            for obj in fakeInputCatalog:
                deltaT=deltaTList[numpy.random.randint(len(deltaTList))]
                scaleArcmin=scalesArcminList[numpy.random.randint(len(scalesArcminList))]
                obj['deltaT']=deltaT
                obj['scaleArcmin']=scaleArcmin
                fakeMapDict['data'], sourceProperties=insertSourceIntoMap(obj, deltaT, scaleArcmin, 
                                                                          fakeMapDict, photometryOptions)
                for key in sourceProperties.keys():
                    obj[key]=sourceProperties[key]
            
            # Complete map pre-processing - i.e. do same background subtraction, point source removal
            maskFileName=diagnosticsDir+os.path.sep+"psMask_%d.fits" % (fakeMapDict['obsFreqGHz'])
            maskingType=unfilteredMapsDictList[0]['pointSourceRemoval']['masking']
            fakeMapDict['data']=mapTools.applyPointSourceMask(maskFileName, fakeMapDict['data'], 
                                                              fakeMapDict['wcs'], mask = maskingType)
                                                              
            # Save here before backgroundSubtraction, in case we want to pass on to Matthew
            astImages.saveFITS(outFileName, fakeMapDict['data'], fakeMapDict['wcs'])
            fakeMapDict['mapFileName']=outFileName
            keysToWrite=['name', 'RADeg', 'decDeg', 'deltaT', 'scaleArcmin', 'YArcmin2']
            keyFormats=['%s', '%.6f', '%.6f', '%.3f', '%.1f', '%.10f']
            extraHeaderText="# Profile = %s, YArcmin2 measured in %.1f arcmin radius circular aperture\n" \
                             % (profileType, photometryOptions['apertureRadiusArcmin'])
            catalogTools.writeCatalog(fakeInputCatalog, outFileName.replace(".fits", ".csv"), keysToWrite, 
                                      keyFormats, [], extraHeaderText = extraHeaderText)
            catalogTools.catalog2DS9(fakeInputCatalog, outFileName.replace(".fits", ".reg"))

            # Ok, now do backgroundSubtraction
            if unfilteredMapsDictList[0]['backgroundSubtraction'] == True:
                fakeMapDict['data']=mapTools.subtractBackground(fakeMapDict['data'], fakeMapDict['wcs'])
            
            fakeInputMapsDictList=[fakeMapDict]
        
            # Filter maps, detect objects, measure fluxes, merge catalogs - in same way as in nemo script
            if os.path.exists(outDir+os.path.sep+"filteredMaps") == True:
                os.system("rm -r %s" % (outDir+os.path.sep+"filteredMaps"))
                os.system("rm -r %s" % (outDir+os.path.sep+"diagnostics"))
            imageDict=mapTools.filterMaps(fakeInputMapsDictList, filtersList, rootOutDir = outDir)
            photometry.findObjects(imageDict, threshold = detectionThresholdSigma, 
                                   minObjPix = detectionMinObjPix, rejectBorder = rejectBorderPix)            
            photometry.measureFluxes(imageDict, photometryOptions, diagnosticsDir, 
                                     unfilteredMapsDict = fakeInputMapsDictList)
            catalogTools.mergeCatalogs(imageDict)
            catalogTools.makeOptimalCatalog(imageDict, minSNToInclude)
                        
            # Match output catalog against input to get recovered fraction
            outputCatalog=imageDict['optimalCatalog']
            simpleCatalogMatch(fakeInputCatalog, outputCatalog, fakeMapDict['beamFWHMArcmin']*2)
             
            # Recovered fraction as fn. of scale
            for obj in fakeInputCatalog:
                for prop in propertiesToTrack:
                    valuesList=selFnDict[profileType][prop]['valuesList']
                    index=valuesList.index(obj[prop])
                    selFnDict[profileType][prop]['totalByRun'][run, index]+=1
                    if obj['recovered'] == True:
                        selFnDict[profileType][prop]['recoveredByRun'][run, index]+=1
                        if prop == propertiesToTrack[0]:    # avoid double counting
                            inputYArcmin2.append(obj['YArcmin2'])
                            recoveredYArcmin2.append(obj['recoveredMatch']['flux_arcmin2'])
                            inputDeltaTc.append(obj['deltaT'])
                            recoveredDeltaTc.append(obj['recoveredMatch']['deltaT_c'])
        
            t1=time.time()
            print "... time taken for fake source insertion & recovery run = %.3f sec" % (t1-t0)
            
        # Bring all results from sim runs together for a given profileType
        for prop in propertiesToTrack:
            fraction=selFnDict[profileType][prop]['recoveredByRun']/selFnDict[profileType][prop]['totalByRun']
            mean=fraction.mean(axis=0)
            stderr=fraction.std(axis=0)/numpy.sqrt(fraction.shape[0])
            selFnDict[profileType][prop]['meanRecoveredFraction']=mean
            selFnDict[profileType][prop]['stderrRecoveredFraction']=stderr            
        
        # Plot for each property
        pylab.close()
        for prop, plotLabel in zip(propertiesToTrack, propertyPlotLabels):
            fraction=selFnDict[profileType][prop]['meanRecoveredFraction']
            errFraction=selFnDict[profileType][prop]['stderrRecoveredFraction']
            valuesList=selFnDict[profileType][prop]['valuesList']
            pylab.plot(valuesList, fraction, 'b-')
            pylab.errorbar(valuesList, fraction, yerr=errFraction, fmt='r.')
            pylab.xlabel(plotLabel)
            pylab.ylabel("Recovered Fraction")
            pylab.ylim(0, 1.05)
            pylab.title("Fake Source Profile = %s" % (profileType))
            outFileName=outDir+os.path.sep+"recoveredFraction_%s_%s.png" % (profileType, prop)
            pylab.savefig(outFileName)
            pylab.close()
        
        # Y recovery plot - residual
        inputYArcmin2=numpy.array(inputYArcmin2)
        recoveredYArcmin2=numpy.array(recoveredYArcmin2)
        diff=inputYArcmin2-recoveredYArcmin2
        norm=1e-3
        labelString="mean(Y$_{in}$-Y$_{out}$) = %.3f $\\times$ 10$^{-3}$ arcmin$^2$\n$\sigma$(Y$_{in}$-Y$_{out}$) = %.3f $\\times$ 10$^{-3}$ arcmin$^2$" % ((diff/norm).mean(), (diff/norm).std())
        pylab.figure(figsize=(10, 8))
        pylab.plot(inputYArcmin2, diff, 'r.')
        pylab.plot(numpy.linspace(inputYArcmin2.min()-1, inputYArcmin2.max()+1, 3), [0]*3, 'k--')
        pylab.xlabel("input Y(r<%d') (arcmin$^2$)" % (photometryOptions['apertureRadiusArcmin']))
        pylab.ylabel("input Y(r<%d') - recovered Y(r<%d') (arcmin$^2$)" % (photometryOptions['apertureRadiusArcmin'], photometryOptions['apertureRadiusArcmin']))
        ax=pylab.gca()
        pylab.text(0.03, 0.03, labelString, transform = ax.transAxes, fontdict = {"size": 14, "linespacing" : 1.5})
        pylab.title("Fake Source Profile = %s" % (profileType))
        pylab.xlim(0, inputYArcmin2.max()*1.1)
        pylab.ylim(-inputYArcmin2.max(), inputYArcmin2.max())
        outFileName=outDir+os.path.sep+"YRecovery_residual_%s.png" % (profileType)
        pylab.savefig(outFileName)
        pylab.close()
        
        # Y recovery plot - correlation
        pylab.figure(figsize=(10, 8))
        oneToOneRange=numpy.linspace(inputYArcmin2.min()*0.5, inputYArcmin2.max()+1, 5)
        pylab.plot(inputYArcmin2, recoveredYArcmin2, 'r.')
        pylab.plot(oneToOneRange, oneToOneRange, 'k--')
        pylab.loglog()
        pylab.xlabel("input Y(r<%d') (arcmin$^2$)" % (photometryOptions['apertureRadiusArcmin']))
        pylab.ylabel("recovered Y(r<%d') (arcmin$^2$)" % (photometryOptions['apertureRadiusArcmin']))
        pylab.title("Fake Source Profile = %s" % (profileType))
        pylab.xlim(inputYArcmin2.min()*0.5, inputYArcmin2.max()*1.5)
        pylab.ylim(inputYArcmin2.min()*0.5, inputYArcmin2.max()*1.5)
        outFileName=outDir+os.path.sep+"YRecovery_correlation_%s.png" % (profileType)
        pylab.savefig(outFileName)
        pylab.close()

        # Delta T recovery plot - residual
        inputDeltaTc=numpy.array(inputDeltaTc)
        recoveredDeltaTc=numpy.array(recoveredDeltaTc)
        diff=inputDeltaTc-recoveredDeltaTc
        labelString="mean($\Delta$T$_{c(in)}$-$\Delta$T$_{c(out)}$) = %.3f $\mu$K\n$\sigma$($\Delta$T$_{c(in)}$-$\Delta$T$_{c(out)}$) = %.3f $\mu$K" % ((diff).mean(), (diff).std())
        pylab.figure(figsize=(10, 8))
        pylab.plot(inputDeltaTc, diff, 'r.')
        pylab.plot(numpy.linspace(inputDeltaTc.min()-1000, inputDeltaTc.max()+1000, 3), [0]*3, 'k--')
        pylab.xlabel("input $\Delta$T$_c$ ($\mu$K)")
        pylab.ylabel("input $\Delta$T$_c$ - recovered $\Delta$T$_c$ ($\mu$K)")
        ax=pylab.gca()
        pylab.text(0.03, 0.03, labelString, transform = ax.transAxes, fontdict = {"size": 14, "linespacing" : 1.5})
        pylab.title("Fake Source Profile = %s" % (profileType))
        pylab.xlim(inputDeltaTc.min()*1.1, inputDeltaTc.max()*1.1)
        pylab.ylim(-(abs(inputDeltaTc).max()*1.1), abs(inputDeltaTc).max()*1.1)
        outFileName=outDir+os.path.sep+"DeltaTc_residual_%s.png" % (profileType)
        pylab.savefig(outFileName)
        pylab.close()        
        
        # Also, work out fudge factor for Y recovery here
        # Insert this into photometry.measureApertureFluxes - obviously we have to turn this off there first
        # if we want to refit this.
        mask=numpy.greater(recoveredYArcmin2, 0)
        #slope, intercept, blah1, blah2, blah3=stats.linregress(recoveredYArcmin2[mask], inputYArcmin2[mask])
        slope, intercept, blah1, blah2, blah3=stats.linregress(numpy.log10(recoveredYArcmin2[mask]), numpy.log10(inputYArcmin2[mask]))
        #rec2=numpy.power(10.0, numpy.log10(recoveredYArcmin2)*slope+intercept)
        
        # Save Y data and fit for this profileType
        outFileName=outDir+os.path.sep+"YRecovery_%s.npz" % (profileType)
        numpy.savez(outFileName, inputYArcmin2, recoveredYArcmin2)
        
    # Save sel. fn. as pickle
    # May want something to convert this into more readable format
    pickleFileName=outDir+os.path.sep+"selFnDict.pickle"
    if os.path.exists(pickleFileName) == True:
        os.remove(pickleFileName)
    pickleFile=file(pickleFileName, "w")
    pickler=pickle.Pickler(pickleFile)
    pickler.dump(selFnDict)
    pickleFile.close()
    
    print "Done, check plots etc., what's going on with recovered Y"    
    ipshell()
    sys.exit()
            
#-------------------------------------------------------------------------------------------------------------
def insertBetaModelIntoMap(objDict, deltaT, scaleArcmin, mapDict, photometryOptions):
    """Inserts a beta model into the map. Adds source properties to the object.
    
    Returns updated map data with source inserted, and sourceProperties dictionary with intergrated Y etc.
    for the objDict.

    """
    
    sourceProperties={} # store things like integrated Y in here
    
    rDegMap=nemoCython.makeDegreesDistanceMap(mapDict['data'], mapDict['wcs'], objDict['RADeg'], 
                                              objDict['decDeg'], (10*scaleArcmin)/60.0)

    # beta fixed, for now
    beta=0.86
    rArcmin=numpy.linspace(0.0, 60.0, 5000)
    smoothArcmin=scaleArcmin
    profile1d=(1.0+(rArcmin/scaleArcmin)**2)**((1.0-3.0*beta)/2)
    
    # Scale to size of input central decrement, before beam smoothing
    profile1d=profile1d*deltaT
        
    # Apply beam as Gaussian filter to profile
    #beamSigma=mapDict['beamFWHMArcmin']/numpy.sqrt(8.0*numpy.log(2.0))            
    #beamSigmaPix=beamSigma/(rArcmin[1]-rArcmin[0])
    #profile1d=ndimage.gaussian_filter1d(profile1d, beamSigmaPix)
    
    # Truncate beyond 10 times core radius
    mask=numpy.greater(rArcmin, 10.0*scaleArcmin)
    profile1d[mask]=0.0
    
    # Turn 1d profile into 2d
    rDeg=rArcmin/60.0
    r2p=interpolate.interp1d(rDeg, profile1d, bounds_error=False, fill_value=0.0)
    profile2d=numpy.zeros(rDegMap.shape)
    mask=numpy.less(rDegMap, 1000)
    profile2d[mask]=r2p(rDegMap[mask])
    
    mapDict['data']=mapDict['data']+profile2d
    
    # What is the integrated Y within the aperture we're using for measuring Ys?
    mask=numpy.less(rDegMap, photometryOptions['apertureRadiusArcmin']/60.0)    
    sumPix=mapTools.convertToY(profile2d[mask], obsFrequencyGHz = mapDict['obsFreqGHz']).sum()
    ra0=objDict['RADeg']
    dec0=objDict['decDeg']
    x, y=mapDict['wcs'].wcs2pix(ra0, dec0)
    ra1, dec1=mapDict['wcs'].pix2wcs(x+1, y+1)    
    xLocalDegPerPix=astCoords.calcAngSepDeg(ra0, dec0, ra1, dec0)
    yLocalDegPerPix=astCoords.calcAngSepDeg(ra0, dec0, ra0, dec1)
    arcmin2PerPix=xLocalDegPerPix*yLocalDegPerPix*60.0**2
    YArcmin2=sumPix*arcmin2PerPix
    sourceProperties['YArcmin2']=YArcmin2
    
    return [mapDict['data'], sourceProperties]

#-------------------------------------------------------------------------------------------------------------
def insertProjectedNFWModelIntoMap(objDict, deltaT, scaleArcmin, mapDict, photometryOptions):
    """Inserts a projected 2d NFW profile (see Koester et al. 2007 and references therein)
    
    """
    
    rs=0.3 # 0.3 Mpc is sensible if R200 = 1.5 Mpc, c = 5, plus Giodini gets that. Koester uses 150 kpc
    r=numpy.linspace(0.0, 10.0, 4000)
    x=r/rs
    fx=numpy.zeros(x.shape)
    mask=numpy.greater(x, 1)
    fx[mask]=1-(2.0/numpy.sqrt(x[mask]**2-1))*numpy.arctan(numpy.sqrt((x[mask]-1)/(x[mask]+1)))
    mask=numpy.less(x, 1)
    fx[mask]=1-(2.0/numpy.sqrt(1-x[mask]**2))*numpy.arctanh(numpy.sqrt((1-x[mask])/(x[mask]+1)))
    mask=numpy.equal(x, 1)
    fx[mask]=0
    mask=numpy.greater(x, 20)
    fx[mask]=0
    sigmax=numpy.zeros(r.shape)
    mask=numpy.greater(r, rs)
    sigmax[mask]=(2*rs*fx[mask])/(x[mask]**2-1)   # ignoring rho_s, which is arbitrary
    
    # Fit power law for extrapolating in centre (NFW profile undefined here)
    mask=numpy.logical_and(numpy.greater(r, rs), numpy.less(r, rs*3))
    deg=1
    p=numpy.polyfit(numpy.log10(r[mask]), numpy.log10(sigmax[mask]), 1)
    fittedFunc=numpy.power(10, p[0]*numpy.log10(r)+p[1])
    sigmax[numpy.less(r, rs)]=fittedFunc[numpy.less(r, rs)]
    sigmax[0]=sigmax[1] # centre is still infinite
    sigmax=sigmax/sigmax.max()
    tckSigmax=interpolate.splrep(r, sigmax) # Note we defined interpolator in terms or r NOT x here!

    #pylab.plot(r, fittedFunc, 'k--')
    
    ipshell()
    sys.exit()
    
#-------------------------------------------------------------------------------------------------------------
def insertArnaudModelIntoMap(objDict, deltaT, scaleArcmin, mapDict, photometryOptions):
    """Inserts an Arnaud (GNFW) model into the map. Adds source properties to the object.
    
    Returns updated map data with source inserted, and sourceProperties dictionary with intergrated Y etc.
    for the objDict.

    """
    
    sourceProperties={} # store things like integrated Y in here
    
    rDegMap=nemoCython.makeDegreesDistanceMap(mapDict['data'], mapDict['wcs'], objDict['RADeg'], 
                                              objDict['decDeg'], (10*scaleArcmin)/60.0)
   
    # The easy way - use Matthew's saved Arnaud GNFW profile and scale it according to scale arcmin
    print "Arnaud model!"
    ipshell()
    sys.exit()
 
    r=numpy.linspace(0.0001, 3, 1000)    # in Mpc
    r500=1.0                        # in Mpc
    
    x=r/r500
        
    P0=8.403
    c500=1.177
    gamma=0.3081
    alpha=1.0510
    beta=5.4905
    
    # dimensionlessP _is_ just the gNFW profile
    dimensionlessP=P0/(numpy.power(c500*x, gamma)*numpy.power((1+numpy.power(c500*x, alpha)), (beta-gamma)/alpha))
        
    # Things get physical here
    z=0.3   # redshift
    M500=5e14
    
    alphaP=0.12
    alphaPPrime=0.10-(alphaP+0.10)*(((x/0.5)**3)/(1.+(x/0.5)**3))
    
    Pr=1.65e-3*astCalc.Ez(z)*numpy.power((M500/3e14), 2.0/3.0+alphaP+alphaPPrime)*dimensionlessP
    
    # Turn from radial profile into projected radial cylindrical profile
    tck_Pr=interpolate.splrep(r, Pr)
    rCyn=numpy.zeros(r.shape)+r
    profCyn=[]
    dr=rCyn[1]-rCyn[0]
    for rc in rCyn:
        dimensionlessP=P0/(numpy.power(c500*x, gamma)*numpy.power((1+numpy.power(c500*x, alpha)), (beta-gamma)/alpha))

        dr = r[1] - r[0]
    y0 = array([profile.get((_x0**2+r**2)**0.5).sum() for _x0 in x0]) / dr
    
    # Convert to angular coords, do cylindrical integral
    rDeg=numpy.degrees(numpy.arctan(r/astCalc.da(z)))
    r2p=interpolate.interp1d(rDeg, Pr, bounds_error=False, fill_value=0.0)
    profile2d=numpy.zeros(rDegMap.shape)
    mask=numpy.less(rDegMap, 1000)
    profile2d[mask]=r2p(rDegMap[mask])
    
    
#-------------------------------------------------------------------------------------------------------------
def simpleCatalogMatch(primary, secondary, matchRadiusArcmin):
    """Simple catalog matching, for finding which fake objects we recovered
    
    Adds 'recovered' key to primary catalog, in place.
    
    """
        
    xMatchRadiusDeg=matchRadiusArcmin/60.0
    sobjRAs=[]
    sobjDecs=[]
    for sobj in secondary:
        sobjRAs.append(sobj['RADeg'])
        sobjDecs.append(sobj['decDeg'])
    sobjRAs=numpy.array(sobjRAs)
    sobjDecs=numpy.array(sobjDecs)
    for pobj in primary:   
        pobj['recovered']=False
        rMin=1e6
        match=None
        rs=astCoords.calcAngSepDeg(pobj['RADeg'], pobj['decDeg'], sobjRAs, sobjDecs)
        rMin=rs.min()
        rMinIndex=numpy.equal(rs, rMin).nonzero()[0][0]
        if rMin < xMatchRadiusDeg:
            match=secondary[rMinIndex]
        if match != None:
            pobj['recovered']=True
            pobj['recoveredMatch']=match
            
#-------------------------------------------------------------------------------------------------------------
def estimateContaminationFromInvertedMaps(imageDict, thresholdSigma, minObjPix, rejectBorder, 
                                          minSNToIncludeInOptimalCatalog, diagnosticsDir = None):
    """Run the whole filtering set up again, on inverted maps.
    
    Writes a DS9. reg file, which contains only the highest SNR contaminants (since these
    are most likely to be associated with artefacts in the map - e.g., point source masking).
    
    Writes a plot and a .fits table to the diagnostics dir.
    
    Returns a dictionary containing the results
    
    """
    
    invertedDict={}
    ignoreKeys=['optimalCatalog', 'mergedCatalog']
    for key in imageDict:
        if key not in ignoreKeys:
            invertedDict[key]=imageDict[key]
    
    photometry.findObjects(invertedDict, threshold = thresholdSigma, minObjPix = minObjPix,
                           rejectBorder = rejectBorder, diagnosticsDir = diagnosticsDir,
                           invertMap = True)    
    catalogTools.mergeCatalogs(invertedDict)
    catalogTools.makeOptimalCatalog(invertedDict, minSNToIncludeInOptimalCatalog)
    catalogTools.catalog2DS9(invertedDict['optimalCatalog'], diagnosticsDir+os.path.sep+"invertedMapsCatalog.reg", constraintsList = ['SNR > 5'])
    
    invertedSNRs=[]
    for obj in invertedDict['optimalCatalog']:
        invertedSNRs.append(obj['SNR'])
    invertedSNRs=numpy.array(invertedSNRs)
    invertedSNRs.sort()
    numInverted=numpy.arange(len(invertedSNRs))+1
     
    candidateSNRs=[]
    for obj in imageDict['optimalCatalog']:
        candidateSNRs.append(obj['SNR'])
    candidateSNRs=numpy.array(candidateSNRs)
    candidateSNRs.sort()
    numCandidates=numpy.arange(len(candidateSNRs))+1
    
    binMin=3.0
    binMax=20.0
    binStep=0.2
    binEdges=numpy.linspace(binMin, binMax, (binMax-binMin)/binStep+1)
    binCentres=(binEdges+binStep/2.0)[:-1]
    candidateSNRHist=numpy.histogram(candidateSNRs, bins = binEdges)
    invertedSNRHist=numpy.histogram(invertedSNRs, bins = binEdges)    
    
    cumSumCandidates=[]
    cumSumInverted=[]
    for i in range(binCentres.shape[0]):
        cumSumCandidates.append(candidateSNRHist[0][i:].sum())
        cumSumInverted.append(invertedSNRHist[0][i:].sum())
    cumSumCandidates=numpy.array(cumSumCandidates, dtype = float)
    cumSumInverted=numpy.array(cumSumInverted, dtype = float)

    xtickLabels=[]
    xtickValues=[]
    fmodMajorTicks=numpy.fmod(binEdges, 5)
    fmodMinorTicks=numpy.fmod(binEdges, 1)
    for i in range(len(binEdges)):
        if fmodMinorTicks[i] == 0:
            xtickValues.append(binEdges[i])
            if fmodMajorTicks[i] == 0:
                xtickLabels.append('%d' % (binEdges[i]))
            else:
                xtickLabels.append('')
            
    # Plot cumulative detections > SNR for both inverted map catalog and actual catalog
    pylab.plot(binEdges[:-1], cumSumInverted, 'r-', label = 'inverted maps')
    pylab.plot(binEdges[:-1], cumSumCandidates, 'b-', label = 'candidates')
    pylab.xlabel("SNR")
    pylab.ylabel("Number > SNR")
    pylab.semilogx()
    pylab.xticks(xtickValues, xtickLabels)
    pylab.xlim(binMin, binMax)
    pylab.legend()
    pylab.savefig(diagnosticsDir+os.path.sep+"cumulativeSNR.png")
    pylab.close()
    
    # Plot cumulative contamination estimate (this makes more sense than plotting purity, since we don't know
    # that from what we're doing here, strictly speaking)
    cumContamination=cumSumInverted/cumSumCandidates
    cumContamination[numpy.isnan(cumContamination)]=0.0
    pylab.plot(binEdges[:-1], cumContamination, 'k-')
    pylab.xlabel("SNR")
    pylab.ylabel("Estimated contamination > SNR")
    pylab.semilogx()
    pylab.xticks(xtickValues, xtickLabels)
    pylab.xlim(binMin, binMax)
    pylab.ylim(0, 1)
    pylab.savefig(diagnosticsDir+os.path.sep+"contaminationEstimateSNR.png")
    pylab.close()    
    
    # Remember, this is all cumulative (> SNR, so lower bin edges)
    contaminDict={}
    contaminDict['SNR']=binEdges[:-1]
    contaminDict['cumSumCandidates']=cumSumCandidates
    contaminDict['cumSumInverted']=cumSumInverted
    contaminDict['cumContamination']=cumContamination       
    
    # Wite a .fits table
    contaminTab=atpy.Table()
    for key in contaminDict.keys():
        contaminTab.add_column(atpy.Column(contaminDict[key], key))
    fitsOutFileName=diagnosticsDir+os.path.sep+"contaminationEstimateSNR.fits"
    if os.path.exists(fitsOutFileName) == True:
        os.remove(fitsOutFileName)
    contaminTab.write(fitsOutFileName)
    
    return contaminDict
    
