"""Playing with the halo mass function... updated for the latest hmf which uses astropy


"""

import os
import sys
import numpy as np
import IPython
import astropy.table as atpy
import pylab as plt
import subprocess
import hmf
from hmf import cosmo
from astropy.cosmology import FlatLambdaCDM
from nemo import simsTools
import cPickle
from scipy import interpolate
from scipy import stats
from astLib import *
import time
plt.matplotlib.interactive(False)

class MockSurvey(object):
    
    def __init__(self, minMass, areaDeg2, zMin, zMax, H0, Om0, Ob0, sigma_8, enableDrawSample = False):
        """Initialise a MockSurvey object. This first calculates the probability of drawing a cluster of 
        given M500, z, assuming the Tinker mass function, and the given (generous) selection limits. 
        An additional selection function can be dialled in later when using drawSample.
        
        NOTE: We've hard coded everything to use M500 wrt critical density at this point.
        
        NOTE: MockSurvey.mf.m has factor of h^-1 in it.
                
        """

        zRange=np.linspace(zMin, zMax, 201)
        areaSr=np.radians(np.sqrt(areaDeg2))**2
                
        # Globally change hmf's cosmology - at least, according to the docs...
        cosmo_model=FlatLambdaCDM(H0 = H0, Om0 = Om0, Ob0 = Ob0)
        cosmo.Cosmology(cosmo_model = cosmo_model)
        
        self.minMass=minMass
        
        # It's much faster to generate one mass function and then update its parameters (e.g., z)
        # NOTE: Mmin etc. are log10 MSun h^-1; dndm is h^4 MSun^-1 Mpc^-3
        # Internally, it's better to stick with how hmf does this, i.e., use these units
        # Externally, we still give  inputs without h^-1
        self.mf=hmf.MassFunction(z = zRange[0], Mmin = 13., Mmax = 16., delta_wrt = 'crit', delta_h = 500.0,
                            sigma_8 = sigma_8, cosmo_model = cosmo_model)#, force_flat = True, cut_fit = False)
            
        self.log10M=np.log10(self.mf.m/self.mf.cosmo.h)
        self.areaSr=areaSr
        self.zBinEdges=zRange
        self.z=(zRange[:-1]+zRange[1:])/2.

        self._doClusterCount()
        
        # Stuff to enable us to draw mock samples:
        # We work with the mass function itself, and apply selection function at the end
        # Selection function gives detection probability for inclusion in sample for given fixed_SNR cut
        # We make draws = self.numClusters 
        # For each draw, we first draw z from the overall z distribution, then log10M from the mass distribution at the given z
        # We then roll 0...1 : if we roll < the detection probability score for M, z cell then we include it in the mock sample 
        if enableDrawSample == True:
            self.enableDrawSample=True
            # For drawing from overall z distribution
            zSum=self.clusterCount.sum(axis = 1)
            pz=np.cumsum(zSum)/self.numClusters
            self.tck_zRoller=interpolate.splrep(pz, self.z)
            # For drawing from each log10M distribution at each point on z grid
            # This can blow up if there are identical entries in pM... so we truncate spline fit when reach 1.0
            # NOTE: Should add sanity check code that the interpolation used here is accurate (make plots)
            print "WARNING: should add code to check interpolation used for drawing mock samples is accurate enough"
            self.tck_log10MRoller=[]
            for i in range(len(self.z)):
                MSum=self.clusterCount[i].sum()
                pM=np.cumsum(self.clusterCount[i])/MSum
                # To avoid multiple pM == 1 blowing up the spline fit (iterate here as could have 2 same, then 2 more same...)
                maxIndex=len(pM)
                while np.equal(pM[:maxIndex], pM[:maxIndex].max()).sum() > 1:
                    if np.equal(pM[:maxIndex], pM[:maxIndex].max()).sum() > 1:
                        maxIndex=np.where(pM[:maxIndex] == pM[:maxIndex].max())[0][0]
                self.tck_log10MRoller.append(interpolate.splrep(pM[:maxIndex], self.log10M[:maxIndex]))
            # Sanity check
            for i in range(len(self.z)):
                if np.any(np.isnan(self.tck_log10MRoller[i][1])) == True:
                    print "nans in self.tck_log10MRoller[%d]" % (i)
                    IPython.embed()
                    sys.exit()
                    
            
    def update(self, H0, Om0, Ob0, sigma_8):
        """Recalculate cluster counts if cosmological parameters updated.
        
        """
        cosmo_model=FlatLambdaCDM(H0 = H0, Om0 = Om0, Ob0 = Ob0)
        self.mf.update(cosmo_model = cosmo_model, sigma_8 = sigma_8)
        self._doClusterCount()
        

    def _doClusterCount(self):
        """Updates cluster count etc. after mass function object is updated.
        
        """
        
        mf=self.mf
        zRange=self.zBinEdges
    
        # Number density by z and total cluster count (in redshift shells)
        # Can use to make P(m, z) plane
        numberDensity=[]
        clusterCount=[]
        totalVolumeMpc3=0.
        for i in range(len(zRange)-1):
            zShellMin=zRange[i]
            zShellMax=zRange[i+1]
            zShellMid=(zShellMax+zShellMin)/2.  
            mf.update(z = zShellMid)
            n=hmf.integrate_hmf.hmf_integral_gtm(mf.m/mf.cosmo.h, mf.dndm*(mf.cosmo.h**4))  # Need to account for h^-1 in mass, h^4 in dndm
            n=abs(np.gradient(n))# Above is cumulative integral (n > m), need this for actual number count 
            numberDensity.append(n)
            shellVolumeMpc3=mf.cosmo.comoving_volume(zShellMax).value-mf.cosmo.comoving_volume(zShellMin).value
            shellVolumeMpc3=shellVolumeMpc3*(self.areaSr/(4*np.pi))
            totalVolumeMpc3=totalVolumeMpc3+shellVolumeMpc3
            clusterCount.append(n*shellVolumeMpc3)
        numberDensity=np.array(numberDensity)
        clusterCount=np.array(clusterCount)  
        self.volumeMpc3=totalVolumeMpc3
        self.numberDensity=numberDensity
        self.clusterCount=clusterCount
        self.numClusters=np.sum(clusterCount)
        self.numClustersByRedshift=np.sum(clusterCount, axis = 1)
        
        
    def addSelFn(self, selFn, tenToA0 = 4.95e-5, B0 = 0.08, Mpivot = 3e14, sigma_int = 0.2):
        """Given SelFn object selFn, calculates completeness over the (self.z, self.mf.M) grid.
        
        Result stored as self.M500Completeness
        
        Can then just multiply by self.clusterCount and sum to get expected number of clusters.
        
        """
        
        self.selFn=selFn
        
        # We may need these elsewhere...
        self.scalingRelationDict={'tenToA0': tenToA0, 'B0': B0, 'Mpivot': Mpivot, 'sigma_int': sigma_int}

        # We should apply the intrinsic scatter in M500 at fixed y0~ somewhere here
        
        # This takes ~95 sec
        print "... calculating (M, z) detection probabilities in each tile (takes ~100 sec on E-D56) ..."
        self.M500Completeness=np.zeros([len(self.selFn.ycLimitTab), self.clusterCount.shape[0], self.clusterCount.shape[1]])
        t0=time.time()        
        ycLimits=self.selFn.ycLimitTab['ycLimit']
        ycErr=ycLimits/self.selFn.SNRCut
        M=(self.mf.m/self.mf.cosmo.h)
        logM=np.log10(M)
        for i in range(len(self.z)):
            z=self.z[i]
            for j in range(M.shape[0]):
                yc, theta500Arcmin, Q=simsTools.y0FromLogM500(logM[j], z, self.selFn.tckQFit, tenToA0 = tenToA0,
                                                              B0 = B0, Mpivot = Mpivot, sigma_int = sigma_int)
                self.M500Completeness[:, i, j]=stats.norm.sf(ycLimits, loc = yc, scale = ycErr)
        t1=time.time()
        
        # This takes ~7.5 sec
        M=(self.mf.m/self.mf.cosmo.h)
        logM=np.log10(M)
        self.M500Completeness_surveyAverage=np.zeros(self.clusterCount.shape)
        for i in range(len(self.z)):
            z=self.z[i]
            ycLimitAtClusterRedshift=selFn.getSurveyAverage_ycLimitAtRedshift(z)
            for j in range(M.shape[0]):
                yc, theta500Arcmin, Q=simsTools.y0FromLogM500(logM[j], z, selFn.tckQFit, tenToA0 = tenToA0,
                                                              B0 = B0, Mpivot = Mpivot, sigma_int = sigma_int)
                ycErr=ycLimitAtClusterRedshift/selFn.SNRCut
                detP=stats.norm.sf(ycLimitAtClusterRedshift, loc = yc, scale = ycErr)
                self.M500Completeness_surveyAverage[i, j]=detP


    def calcNumClustersExpected(self, M500Limit = 0.1, zMin = 0.0, zMax = 2.0, applySelFn = False, 
                                useSurveyAverageSelFn = True):
        """Calculate the number of clusters expected above a given mass limit. If applySelFn = True, apply
        the selection function (in which case M500Limit isn't important, so long as it is low).
        
        NOTE: units of M500Limit are 1e14 MSun.
        
        """
        
        if applySelFn == True:
            if useSurveyAverageSelFn == True:
                numClusters=self.M500Completeness_surveyAverage*self.clusterCount
            else:
                numClusters=0
                for i in range(len(self.selFn.ycLimitTab)):
                    numClusters=numClusters+self.M500Completeness[i]*self.clusterCount*self.selFn.ycLimitTab['fracSurveyArea'][i]
        else:
            numClusters=self.clusterCount
        
        zMask=np.logical_and(np.greater(self.z, zMin), np.less(self.z, zMax))
        mMask=np.greater(self.mf.m/self.mf.cosmo.h, M500Limit*1e14)
        
        return numClusters[:, mMask][zMask].sum()
        

    def getPLog10M(self, z):
        """Returns P(log10M) at given z, which corresponds to self.log10M.
        
        """

        self.mf.update(z = z)
        numberDensity=hmf.integrate_hmf.hmf_integral_gtm(self.mf.m, self.mf.dndm)
        PLog10M=numberDensity/np.trapz(numberDensity, self.mf.m)

        return PLog10M
    
    
    def drawSample(self):
        """Draw a cluster sample from the MockSurvey, applying the survey averaged
        selection function when doing so.
        
        Returns an astropy Table object, with columns name, redshift, M500
        
        NOTE: units of M500 are 1e14 MSun
        
        """
        
        # Survey-averaged, so here is the 1-sigma noise on y0~
        y0Noise=self.selFn.ycLimit_surveyAverage.mean()/self.selFn.SNRCut
        
        # This takes ~16 sec for [200, 300]-shaped z, log10M500 grid
        #t0=time.time()
        mockCatalog=[]
        for i in range(int(self.numClusters)):
            #print "... %d/%d ..." % (i, self.numClusters)
            # Draw z
            zRoll=np.random.uniform(0, 1)
            z=interpolate.splev(zRoll, self.tck_zRoller)
            zIndex=np.where(abs(self.z-z) == abs(self.z-z).min())[0][0]
            # Draw M|z
            MRoll=np.random.uniform(0, 1)
            log10M=interpolate.splev(MRoll, self.tck_log10MRoller[zIndex])
            log10MIndex=np.where(abs(self.log10M-log10M) == abs(self.log10M-log10M).min())[0][0]
            # Apply selection function
            detP=self.M500Completeness_surveyAverage[zIndex, log10MIndex]
            PRoll=np.random.uniform(0, 1)
            if PRoll < detP:
                # y0 from M500... we add scatter (both intrinsic and from error bar) here
                # We should then feed this back through to get what our inferred mass would be...
                # (i.e., we go true mass -> true y0 -> "measured" y0 -> inferred mass)
                # NOTE: we've applied the selection function, so we've already applied the noise...?
                true_y0, theta500Arcmin, Q=simsTools.y0FromLogM500(log10M, z, self.selFn.tckQFit)
                measured_y0=np.exp(np.random.normal(np.log(true_y0), self.scalingRelationDict['sigma_int']))
                measured_y0=np.random.normal(measured_y0, y0Noise)
                measured_y0=true_y0+np.random.normal(0, y0Noise)
                # inferred mass (full blown M500 UPP, mass function shape de-biased)
                # NOTE: we may get confused about applying de-biasing term or not here...
                M500Dict=simsTools.calcM500Fromy0(measured_y0, y0Noise, z, 0.0, 
                                                  tenToA0 = self.scalingRelationDict['tenToA0'],
                                                  B0 = self.scalingRelationDict['B0'],
                                                  Mpivot = self.scalingRelationDict['Mpivot'], 
                                                  sigma_int = self.scalingRelationDict['sigma_int'],
                                                  tckQFit = self.selFn.tckQFit, mockSurvey = self, 
                                                  applyMFDebiasCorrection = True, calcErrors = True)
                # Add to catalog
                objDict={'name': 'MOCK-CL%d' % (i+1), 
                         'true_M500': np.power(10, log10M)/1e14,
                         'true_fixed_y_c': true_y0/1e-4,
                         'fixed_y_c': measured_y0/1e-4,
                         'err_fixed_y_c': y0Noise/1e-4,
                         'fixed_SNR': measured_y0/y0Noise,
                         'redshift': float(z),
                         'redshiftErr': 0}
                for key in M500Dict:
                    objDict[key]=M500Dict[key]
                mockCatalog.append(objDict)
        
        # Convert to table
        # NOTE: left out Uncorr confusion for now... i.e., we use 'Uncorr' here...
        tab=atpy.Table()
        keyList=['name', 'redshift', 'redshiftErr', 'true_M500', 'true_fixed_y_c', 'fixed_SNR', 'fixed_y_c', 'err_fixed_y_c', 
                 'M500Uncorr', 'M500Uncorr_errPlus', 'M500Uncorr_errMinus']
        for key in keyList:
            arr=[]
            for objDict in mockCatalog:
                arr.append(objDict[key])
            tab.add_column(atpy.Column(arr, key))
        
        return tab
        
