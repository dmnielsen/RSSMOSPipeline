#!/usr/bin/env python

"""Pipeline for reducing SALT RSS MOS data, using the stuff that comes in the product/ dir.

"""

import os
import sys
import numpy as np
import matplotlib.pyplot as plt
import pyfits
import glob
import time
import datetime
from astropy.table import Table,Column
#from astLib import *
from scipy import interpolate
from scipy import ndimage
from scipy import optimize
import pickle
import IPython
#plt.matplotlib.interactive(True)

#-------------------------------------------------------------------------------------------------------------
LOGFILE=None
REF_MODEL_DIR="modelArcSpectra"

#-------------------------------------------------------------------------------------------------------------
def trace(message, verbose = True, logFile = None):
    """Simple trace function. Prints to screen if verbose == True, prints to logFile if logFile is not None.
    Automatically adds newline characters to end of message.
    
    """  
    if verbose == True:
        print message
    if logFile != None:
        logFile.write(message+"\n")
        
#-------------------------------------------------------------------------------------------------------------
def listToString(inList, prefix = "", delimiter=","):
    """Converts a list into a comma delimited string, can add prefixes if needed.
    
    """
    outString=""
    for i in inList:
        if outString != "":
            outString=outString+delimiter
        outString=outString+prefix+str(i)
    return outString

#-------------------------------------------------------------------------------------------------------------
def listToFile(inList, fileName, extension = None):
    """Converts a list of file names into a file that can be used in iraf with the @ syntax.
    
    Returns the file name of the list file, with @ prefix added already.
    
    """
    
    listFile=file(fileName, "w")
    for inFile in inList:
        if extension == None:
            listFile.write(inFile+"\n")
        else:
            listFile.write(inFile+extension+"\n")
    listFile.close()

    return "@"+fileName

#-------------------------------------------------------------------------------------------------------------
def makeOutputFileNameList(inFileNameList, prefix, outDir):
    """Generates output file names in the form outDir/<prefix>filename.fits for all file names in the 
    inFileNameList
    
    """
    
    outList=[]
    for inFileName in inFileNameList:
        outList.append(makeOutputFileName(inFileName, prefix, outDir))
    return outList
    
#-------------------------------------------------------------------------------------------------------------
def makeOutputFileName(inputFileName, prefix, outDir):
    """Given a raw file name in the form outDir/filename.fits, generate output filename in the form
    reduced/<prefix>filename.fits
    
    """
    
    return outDir+os.path.sep+prefix+os.path.split(inputFileName)[-1]

#-------------------------------------------------------------------------------------------------------------
def splitMEF(MEFFileName, rootOutFileName):
    """This splits a MEF.
    
    """
    
    img=pyfits.open(MEFFileName)
    for i in range(len(img)):
        if img[i].name == 'SCI':
            newImg=pyfits.HDUList()
            hdu=pyfits.PrimaryHDU(None, img[i].header)   
            hdu.data=img[i].data
            newImg.append(hdu)
            newImg.writeto(rootOutFileName.replace(".fits", "_%d.fits" % (i)), clobber = True)    

#------------------------------------------------------------------------------------------------------------------
def getImageInfo(rawDir):
    """Sorts through all .fits files, making lists of biases, flats, science frames, arc frames, for each 
    night of observations, grating, position combo. Returns a dictionary of lists.
    
    NOTE: Explicitly avoiding standard stars for now 
    
    """
 
    # Based on the Gemini pipeline
    # Here, organised by maskID -> filetype (we have flats/arcs for every occurance)
    infoDict={}
    files=glob.glob(rawDir+os.path.sep+"mbxgp*.fits")  # Files either GMOS N or S depending on mask name
    for f in files:
        img=pyfits.open(f)
        header=img[0].header
        if header['OBSMODE'] == 'SPECTROSCOPY' and header['MASKTYP'] == 'MOS':
 
            dateObs=header['DATE-OBS']
            timeObs=header['TIME-OBS']
            maskID=header['MASKID']
            if maskID not in infoDict.keys():
                infoDict[maskID]={}  
            #if dateObs not in infoDict[maskID].keys():
                #infoDict[maskID][dateObs]={}
                    
            obsType=header['CCDTYPE']
            #if obsType not in infoDict[maskID][dateObs].keys():
                #infoDict[maskID][dateObs][obsType]=[]
            #infoDict[maskID][dateObs][obsType].append(f)
            if obsType not in infoDict[maskID].keys():
                infoDict[maskID][obsType]=[]
            infoDict[maskID][obsType].append(f)
            
            if obsType == 'OBJECT':
                infoDict[maskID]['objectName']=header['OBJECT'] # This can be 'flat' so is less useful than MASKID
                infoDict[maskID]['maskName']=maskID             # Clunky, but convenient
                
    return infoDict

#-------------------------------------------------------------------------------------------------------------
def makeMasterFlats(maskDict, outDir, deltaHours = 0.5):
    """Make master flats from files in 'FLAT' key of maskDict. Automatically group flats taken within
    deltaHours. Adds paths to 'masterFlat' key.
    
    """
    
    flatLists=groupFilesListByTime(maskDict['FLAT'])
        
    maskDict['masterFlats']=[]

    print ">>> Making masterFlats (it is a good idea to sin bin any that aren't aligned with object spectra at cutting stage)"
    
    for i in range(len(flatLists)):
        flatFiles=flatLists[i]
        masterFlatPath=outDir+os.path.sep+"masterFlat_%d.fits" % (i)
        print "... making %s (%s) ..." % (masterFlatPath, flatFiles)
        if os.path.exists(masterFlatPath) == False:
            flatCube=[]
            for f in flatFiles:
                img=pyfits.open(f)
                flatCube.append(img['SCI'].data)
            flatCube=np.array(flatCube)
            flatData=np.median(flatCube, axis = 0)
            img['SCI'].data=flatData
            img.writeto(masterFlatPath, clobber = True)
        maskDict['masterFlats'].append(masterFlatPath)
        
#-------------------------------------------------------------------------------------------------------------
def getCTimeFromHeader(fileName):
    """Get unix ctime from header DATE-OBS and TIME-OBS keywords.
    
    Returns ctime (accurate to second level only)
    
    """
    
    img=pyfits.open(fileName)
    header=img[0].header
    dateObs=header['DATE-OBS']
    timeObs=header['TIME-OBS']
    y, m, d=dateObs.split("-")
    y=int(y)
    m=int(m)
    d=int(d)
    h, mn, s=timeObs.split(":")
    h=int(h)
    mn=int(mn)
    sec=int(s.split(".")[0])
    usec=int(s.split(".")[-1])*1000
    dateTimeObs=datetime.datetime(y, m, d, h, mn, sec, usec)
    ctime=int(dateTimeObs.strftime("%s"))
    
    return ctime

#-------------------------------------------------------------------------------------------------------------
def groupFilesListByTime(filesList, deltaHours = 0.5):
    """Given a list of files, splits it such that they are in groups covered by deltaHours.
    
    """
    
    ctimes=[]
    for f in filesList:
        ctime=getCTimeFromHeader(f)
        ctimes.append(ctime)  # 1 second accuracy is enough for us
        
    outFileLists=[]
    outCTimeLists=[]
    for fileName, ctime in zip(filesList, ctimes):
        foundList=False
        for cl, fl in zip(outCTimeLists, outFileLists):
            for c, f in zip(cl, fl):
                if abs(c-ctime) < 3600.0*deltaHours:
                    if ctime not in cl and fileName not in fl:
                        cl.append(ctime)
                        fl.append(fileName)
                    foundList=True
        if foundList == False:
            outFileLists.append([])
            outCTimeLists.append([])
            outFileLists[-1].append(fileName)
            outCTimeLists[-1].append(ctime)
            
    return outFileLists
    
#-------------------------------------------------------------------------------------------------------------
def cutIntoSlitLets(maskDict, outDir):
    """Cuts files into slitlets, making MEF files. 
        
    NOTE: assuming slits_0.txt file applies across all images for now.
    
    """

    # We find slits using master flats, and store slit locations in a dictionary indexed by masterFlatPath
    # We have a routine to pull out a corresponding master flat (and hence slitsDict) for every image.
    maskDict['slitsDicts']={}
    for i in range(len(maskDict['masterFlats'])):
        masterFlatPath=maskDict['masterFlats'][i]
        cutMasterFlatPath=masterFlatPath.replace("masterFlat", "cmasterFlat")
        slitsDict=findSlits(masterFlatPath)
        maskDict['slitsDicts'][masterFlatPath]=slitsDict
        outFileName=makeOutputFileName(masterFlatPath, "c", outDir)
        cutSlits(masterFlatPath, outFileName, slitsDict)

    toCutList=maskDict['OBJECT']+maskDict['ARC']
    outCutList=makeOutputFileNameList(toCutList, "c", outDir)
    print ">>> It is a good idea to check that for the below the corresponding DS9 .reg file aligns with the slits and that object spectra are actually centred in the slits..."
    for f, outFileName in zip(toCutList, outCutList):
        masterFlatPath=findMatchingFileByTime(f, maskDict['masterFlats'])
        slitsDict=maskDict['slitsDicts'][masterFlatPath]
        print "... cutting %s using %s for slit definition ..." % (f, masterFlatPath)
        cutSlits(f, outFileName, slitsDict)
        # Cut master flat too - for doing the actual flat fielding later...
        cutMasterFlatPath=masterFlatPath.replace("masterFlat", "cut_masterFlat")
        if os.path.exists(cutMasterFlatPath) == False:
            cutSlits(masterFlatPath, cutMasterFlatPath, slitsDict)
    
    # Write out a .reg file so we can match slits to objects
    img=pyfits.open(f)
    centreColumn=img['SCI'].header['NAXIS1']/2
    img.close()
    for masterFlatPath in maskDict['slitsDicts'].keys():
        slitsDict=maskDict['slitsDicts'][masterFlatPath]
        regFileName=outDir+os.path.sep+os.path.split(masterFlatPath)[-1].replace(".fits", "_slitLocations.reg")
        outFile=file(regFileName, "w")
        outFile.write("# DS9 region file\n")
        outFile.write('global color=green dashlist=8 3 width=1 font="helvetica 10 normal" select=1 highlite=1 dash=0 fixed=0 edit=1 move=1 delete=1 include=1 source=1\n')
        outFile.write("image\n")
        for key in slitsDict.keys():
            outFile.write("point(%.3f,%.3f) # point=boxcircle text={SLIT%d}\n" % (centreColumn, (slitsDict[key]['yMax']+slitsDict[key]['yMin'])/2.0, key))            
            outFile.write("box(%.1f,%.1f,%.1f,%.1f)\n" % (centreColumn, (slitsDict[key]['yMax']+slitsDict[key]['yMin'])/2.0+1, centreColumn*2.0, (slitsDict[key]['yMax']-slitsDict[key]['yMin'])))
        outFile.close() 

#-------------------------------------------------------------------------------------------------------------
def cutSlits(inFileName, outFileName, slitsDict):
    """Makes a MEF file containing slitlets.
    
    """
            
    img=pyfits.open(inFileName)
    d=img['SCI'].data
            
    newImg=pyfits.HDUList()
    hdu=pyfits.PrimaryHDU(None, img[0].header)
    newImg.append(hdu)
    for slitKey in slitsDict.keys():
        slitData=d[slitsDict[slitKey]['yMin']:slitsDict[slitKey]['yMax']]
        hdu=pyfits.ImageHDU(data = slitData, header = None, name = 'SLIT%d' % (slitKey))
        newImg.append(hdu)
        
    if os.path.exists(outFileName):
        os.remove(outFileName)
        
    newImg.writeto(outFileName)
    newImg.close()
    
#-------------------------------------------------------------------------------------------------------------
def findSlits(flatFileName):
    """Our replacement for the specslit, which seems no longer to give sensible output on the 2012B data.
        
    Returns a dictionary which can be fed into cutSlits
    
    """ 
    
    #---
    img=pyfits.open(flatFileName)
    d=img['SCI'].data
    
    # Take out spectrum of flat lamp (approx)
    a=np.median(d, axis = 0)
    d=d/a
    d[np.isnan(d)]=0.0

    # Use grad to find edges
    prof=np.median(d, axis = 1)
    grad=np.gradient(prof)

    threshold=0.1
    featureMinPix=3
    plusMask=np.greater(grad, threshold)
    minusMask=np.less(grad, threshold*-1)
    
    # This looks for alternating +/-, but will merge slits which butt up against each other
    slitsDict={}
    lookingFor=1
    yMin=None
    yMax=None
    slitCount=0
    for i in range(len(plusMask)):
        if lookingFor == 1:
            if plusMask[i] == True:
                yMin=i
                lookingFor=0
        if lookingFor == 0:
            if minusMask[i] == True:
                yMax=i
                lookingFor=1
        if yMin != None and yMax != None:
            # Does this need deblending?
            #if yMin > 1360 and yMax < 1480:
                #IPython.embed()
                #sys.exit()
            slitCount=slitCount+1
            slitsDict[slitCount]={'yMin': yMin, 'yMax': yMax}    
            yMin=None
            yMax=None
    
    #IPython.embed()
    #sys.exit()
    
    #---
    # Below works on A3827 - delete once tested with new algorithm
    #img=pyfits.open(flatFileName)
    #d=img['SCI'].data
    #prof=np.median(d, axis = 1)
    #threshold=np.median(prof)*2
        
    #sigPix=np.array(np.greater(prof, threshold), dtype=int)
    #sigPixMask=np.equal(sigPix, 1)
    #segmentationMap, numObjects=ndimage.label(sigPix)
    #maxSlits=segmentationMap.max()

    #slitsDict={}
    #for i in range(1, maxSlits+1):
        #indices=np.where(segmentationMap == i)[0]
        #slitsDict[i]={'yMin': indices.min(), 'yMax': indices.max()}

    ## Sanity check plot
    #plt.plot(prof)
    #plt.plot([np.median(prof)*2]*len(prof), 'r-')
    #IPython.embed()
    #sys.exit()

    return slitsDict

#-------------------------------------------------------------------------------------------------------------
def applyFlatField(maskDict, outDir):
    """Applies the flat field correction. Let's do this in place...
    
    """

    print ">>> Applying flat field..."
    
    toFlatList=makeOutputFileNameList(maskDict['OBJECT'], "c", outDir)
    for f in toFlatList:
        masterFlatPath=findMatchingFileByTime(f, maskDict['masterFlats'])
        cutMasterFlatPath=masterFlatPath.replace("masterFlat", "cut_masterFlat")
        img=pyfits.open(f)
        flatImg=pyfits.open(cutMasterFlatPath)
            
        extensionsList=[]
        for hdu in img:
            if "SLIT" in hdu.name:
                extensionsList.append(hdu.name)
        
        for extension in extensionsList:
            data=img[extension].data
            flatfield=flatImg[extension].data
            med=np.median(flatfield, axis = 0)
            
            # Find chip gaps
            threshold=200
            grad=np.gradient(med)
            plusMask=np.greater(grad, threshold)
            minusMask=np.less(grad, -1*threshold) 
            gapsDict={}
            lookingFor=0
            xMin=None
            xMax=None
            gapsCount=0
            for i in range(len(plusMask)):
                if lookingFor == 1:
                    if plusMask[i] == True:
                        xMax=i
                        lookingFor=0
                if lookingFor == 0:
                    if minusMask[i] == True:
                        xMin=i
                        lookingFor=1
                if xMin != None and xMax != None:
                    gapsCount=gapsCount+1
                    gapsDict[gapsCount]={'xMin': xMin-1, 'xMax': xMax+1}    
                    xMin=None
                    xMax=None
            gapsMask=np.ones(len(med), dtype = bool)
            for key in gapsDict.keys():
                gapsMask[gapsDict[key]['xMin']:gapsDict[key]['xMax']]=False
            
            # Fit and remove spectrum of flat lamp
            x=np.arange(len(med))
            poly=np.poly1d(np.polyfit(x[gapsMask], med[gapsMask], 10))
            mod=np.array([poly(x)]*data.shape[0])
            flatfield=flatfield/mod
            #plt.plot(x, med)
            #plt.plot(x, poly(x))
            #IPython.embed()
            #sys.exit()
            
            data=data/flatfield
            data[np.isnan(data)]=0.0
            img[extension].data=data
        
        img.writeto(f, clobber = True)

#-------------------------------------------------------------------------------------------------------------
def findMatchingFileByTime(inputFileName, possibleFilesList):
    """Identify the file name in possibleFilesList that is closest to inputFileName in terms of time.
    Use to find corresponding arcs, flats.
    
    Returns fileName
    
    """
        
    ctimes=[]
    for f in possibleFilesList:
        ctime=getCTimeFromHeader(f)
        ctimes.append(ctime)
    ctimes=np.array(ctimes)
    
    fileCTime=getCTimeFromHeader(inputFileName)
    bestMatchIndex=np.where(abs(fileCTime-ctimes) == abs(fileCTime-ctimes).min())[0][0]
    
    return possibleFilesList[bestMatchIndex]

#-------------------------------------------------------------------------------------------------------------
def flattenAndRectify(maskDict, outDir, subtractSky = False):
    """Apply the appropriate flat field and rectification transform to all arc and science frames.
    
    """
        
    # Flatten arcs and science frames, applying rectification as we go
    # We need to work out the appropriate transform to use on a file-by-file basis
    # Do this by matching to nearest arc in time
    toFlatList=maskDict['ARC']+maskDict['OBJECT']
    toFlatList=makeOutputFileNameList(toFlatList, "c", outDir)
    outFlattenedList=makeOutputFileNameList(toFlatList, "f", outDir)
    for f, outFileName in zip(toFlatList, outFlattenedList):

        # Find nearest masterFlat in time
        masterFlatPath=findMatchingFileByTime(f, maskDict['masterFlats'])
        cutMasterFlatPath=makeOutputFileName(masterFlatPath, "c", outDir)
        try:
            masterFlatImg=pyfits.open(cutMasterFlatPath)
        except:
            print "Hmm - can't find cutMasterFlatPath"
            IPython.embed()
            sys.exit()
        
        # Find nearest arc in time
        arcPath=findMatchingFileByTime(f, maskDict['ARC'])        
        
        toFlatImg=pyfits.open(f)
        for i in range(len(toFlatImg)):
            if "SLIT" in toFlatImg[i].name:
                
                toFlatData=toFlatImg[i].data
                flatData=masterFlatImg[i].data
                
                # Use response instead of vanilla flat fielding
                # If we don't remove these files, we end up appending lots of extensions
                if os.path.exists("resp_in.fits") == True:
                    os.remove("resp_in.fits")
                if os.path.exists("resp_out.fits") == True:
                    os.remove("resp_out.fits")
                newImg=pyfits.HDUList()
                hdu=pyfits.PrimaryHDU(None, masterFlatImg[i].header)   
                hdu.header.update("DISPAXIS", 1)
                hdu.data=flatData
                newImg.append(hdu)
                newImg.writeto("resp_in.fits", clobber = True)  
                iraf.flpr() # give time to write resp_in.fits 
                longslit.response(calibration="resp_in.fits", normalization="resp_in.fits", 
                                    response="resp_out.fits", interactive="no")
                respData=pyfits.getdata("resp_out.fits")
                toFlatImg[i].data=(toFlatData/respData)#*(np.mean(flatData))
                toFlatImg[i].data=np.nan_to_num(toFlatImg[i].data)
                
                # Rectify
                xCorrTransform=maskDict['xCorrTransforms'][arcPath][toFlatImg[i].name]
                toFlatImg[i].data=applyXCorrRectification(toFlatImg[i].data, xCorrTransform)    
                
                # Subtract sky here on science frames - should make CR rejection easier
                # Take sky as everything below median + 1 sigma
                if toFlatImg[0].header['CCDTYPE'] == 'OBJECT' and subtractSky == True:
                    # Need to do something a little more sophisticated to handle chip gaps
                    gapMask=np.less(toFlatImg[i].data, 1.0)
                    gapMask=np.greater(ndimage.uniform_filter(np.array(gapMask, dtype = float), 5), 0.1) # dilation
                    prof=np.median(toFlatImg[i].data, axis = 1)
                    med=np.median(prof)
                    std=np.std(prof)
                    yMask=np.less(prof, med+std)
                    sky=np.median(toFlatImg[i].data[yMask], axis = 0)
                    skyImage=np.array([sky]*toFlatImg[i].data.shape[0])
                    toFlatImg[i].data=toFlatImg[i].data-skyImage
                    toFlatImg[i].data[gapMask]=0.0
                                    
        toFlatImg.writeto(outFileName, clobber = True)

#-------------------------------------------------------------------------------------------------------------
def detectLines(data, sigmaCut = 3.0, thresholdSigma = 2.0, featureMinPix = 30):
    """Detect lines in a 2d arc spectrum. Uses the central row of the 2d spectrum only.
    
    Returns: featureTable, segmentationMap
    
    """
    
    # Detect arc lines
    mean=0
    sigma=1e6
    for i in range(20):
        nonZeroMask=np.not_equal(data, 0)
        mask=np.less(abs(data-mean), sigmaCut*sigma)
        mask=np.logical_and(nonZeroMask, mask)
        mean=np.mean(data[mask])
        sigma=np.std(data[mask])
    detectionThreshold=thresholdSigma*sigma
    mask=np.greater(data-mean, detectionThreshold)

    # Get feature positions, number of pixels etc.
    # Find features in 2d, match to wavelength coord in centre row
    segmentationMap, numObjects=ndimage.label(mask)
    sigPixMask=np.equal(mask, 1)
    objIDs=np.unique(segmentationMap)
    objNumPix=ndimage.sum(sigPixMask, labels = segmentationMap, index = objIDs)
    objPositions_centreRow=ndimage.center_of_mass(data[data.shape[0]/2], labels = segmentationMap, index = objIDs)
    objPositions_centreRow=np.array(objPositions_centreRow).flatten()
    minPixMask=np.greater(objNumPix, featureMinPix)
    featureTable=Table()
    featureTable.add_column(Column(objIDs[minPixMask],name='id'))
    featureTable.add_column(Column(objPositions_centreRow[minPixMask],name='x_centreRow'))
    featureTable.add_column(Column([data.shape[0]/2]*len(featureTable),name='y_centreRow'))
    featureTable.add_column(Column(data[data.shape[0]/2, np.array(np.round(featureTable['x_centreRow']), dtype = int)],name='amplitude'))

    return featureTable, segmentationMap

#-------------------------------------------------------------------------------------------------------------
def fftCorrelate(f, g):
    """Does zero-padded fft correlation between arrays f, g.
    
    Returns corr, corrMax, shift
    
    """
    
    # Upsampling doesn't help unless go factor of several
    upSample=10.0
    fUp=ndimage.zoom(f, upSample)
    gUp=ndimage.zoom(g, upSample)
    
    # Zero padding
    numPaddedSamples=len(fUp)*2
    n=None
    for i in range(1, 30):
        if 2**i > numPaddedSamples:
            n=i
            break
    if n == None:
        raise Exception, "Wavelength range covered is too big!"
    fPadded=np.zeros(2**n)
    fPadded[fUp.shape[0]/2:fUp.shape[0]/2+fUp.shape[0]]=fUp[:]
    gPadded=np.zeros(2**n)
    gPadded[gUp.shape[0]/2:gUp.shape[0]/2+gUp.shape[0]]=gUp[:]
    
    # FFT correlate
    fTemplate=np.fft.fft(fPadded)
    fSpectrum=np.fft.fft(np.fft.fftshift(gPadded))
    fxCorr=fTemplate*fSpectrum.conj()
    corr=np.fft.ifft(fxCorr).real
    
    # Get shift, accounting for zero padding and upsampling
    corrMax=corr.max()
    corrMaxIndex=float(np.argmax(corr))
    shift=(corrMaxIndex-float(fPadded.shape[0])/2.-1)/upSample
    
    return corr, corrMax, shift

#-------------------------------------------------------------------------------------------------------------
def minFunc_findScale(s, shift, arcRow, normRefModel, data_x):
    """For optimize.minimise - return 1/corrMax
    
    """
    
    tck=interpolate.splrep((data_x+shift)+s*data_x, arcRow)
    arcRow_scaled=interpolate.splev(data_x, tck, ext = 1)
    arcMean=np.mean(arcRow_scaled)
    arcStd=np.std(arcRow_scaled)
    
    # Old style: minimize what we call overlap below - this behaves well with optimize.minimize
    # (overlap vs scale is a function with a clear minimum)
    overlap=np.trapz(abs(normRefModel[:data_x.shape[0]]-(arcRow_scaled-arcMean)/arcStd))

    return overlap

#-------------------------------------------------------------------------------------------------------------
def findScaleAndShift(arcRow, refModelDict):
    """Find best fit stretch and scale to transform arcRow to reference model
    
    """
    
    # Use cross correlation to get initial guess at shift between arc and model
    shift=np.argmax(np.correlate(refModelDict['arc_centreRow'], arcRow, mode = 'full'))-refModelDict['arc_centreRow'].shape[0]
    
    # New optimize based method (robust when used with overlap method, but not with xcorr)
    data_x=np.arange(0, arcRow.shape[0])        
    modelStd=np.std(refModelDict['arc_centreRow'])
    modelMean=np.mean(refModelDict['arc_centreRow'])
    normRefModel=(refModelDict['arc_centreRow']-modelMean)/modelStd
    
    result=optimize.minimize_scalar(minFunc_findScale, bounds = (-0.02, 0.02), method = 'Bounded', 
                                    args = (shift, arcRow, normRefModel, data_x))
    s=result['x']
    
    # xcorr is still best to use for selecting between models with corrMax
    tck=interpolate.splrep((data_x+shift)+s*data_x, arcRow)
    arcRow_scaled=interpolate.splev(data_x, tck, ext = 1)
    arcMean=np.mean(arcRow_scaled)
    arcStd=np.std(arcRow_scaled)
    corr, corrMax, extraShift=fftCorrelate(normRefModel, (arcRow_scaled-arcMean)/arcStd)    
    
    return corrMax, s, shift
  
#-------------------------------------------------------------------------------------------------------------
def selectBestRefModel(modelFileNameList, arcData, thresholdSigma = 2.0):
    """Returns the reference arc model which maximises the cross correlation with the arc data.
    
    """

    bestCorrMaxList=[]
    bestFitShiftList=[]
    bestFitScaleList=[]
    refModelDictsList=[]
    arcFeatureTablesList=[]
    arcSegMapsList=[]
    fitDictList=[]
    for modelFileName in modelFileNameList:
    
        # Load reference model
        pickleFile=file(modelFileName, "r")
        unpickler=pickle.Unpickler(pickleFile)
        refModelDict=unpickler.load()
        refModelDictsList.append(refModelDict)
        pickleFile.close()
        
        # First need to find arc features
        arcFeatureTable, arcSegMap=detectLines(arcData, thresholdSigma = thresholdSigma)
        arcFeatureTablesList.append(arcFeatureTable)
        arcSegMapsList.append(arcSegMap)
        
        # Replaced np.correlate with fft based correlation
        # Find shift and wavelength dependent scale change (stretch, then shift)
        arcRow=arcData[arcData.shape[0]/2]
        bestCorrMax, bestFitScale, bestFitShift=findScaleAndShift(arcRow, refModelDict)
        bestCorrMaxList.append(bestCorrMax)
        bestFitScaleList.append(bestFitScale)
        bestFitShiftList.append(bestFitShift)
 
    # So... which arc model worked best? Use it...
    bestModelIndex=np.argmax(bestCorrMaxList)
    refModelDict=refModelDictsList[bestModelIndex]
    arcFeatureTable=arcFeatureTablesList[bestModelIndex]
    arcSegMap=arcSegMapsList[bestModelIndex]
    bestFitScale=bestFitScaleList[bestModelIndex]
    bestFitShift=bestFitShiftList[bestModelIndex]
    
    return refModelDict, arcFeatureTable, arcSegMap
    
#-------------------------------------------------------------------------------------------------------------
def findWavelengthCalibration(arcData, modelFileName, sigmaCut = 3.0, thresholdSigma = 2.0, 
                              featureMinPix = 50, order = 2, diagnosticsDir = None, diagnosticsLabel = None):
    """Find wavelength calibration for .fits image arcFileName containing a 2d arc spectrum.
    
    modelFileName is the path to a model made by makeModelArcSpectrum.
    
    Returns an array of polynomial fit coefficients that can be fed into wavelengthCalibrateAndRectify
    
    """

    # We now allow multiple reference models for each grating/lamp/binning config
    # This is useful if e.g., we have some MOS slit which is way to the red/blue end of the detector
    # First select the model to use based on centre row only (saves much time)
    # Choose best from maximum cross correlation
    modelFileNameList=glob.glob(modelFileName.split(".pickle")[0]+"*.pickle")
    refModelDict, arcFeatureTable, arcSegMap=selectBestRefModel(modelFileNameList, arcData, 
                                                                thresholdSigma = thresholdSigma)

    # Continue with previous 2d wavelength calib method
    yIndex=arcData.shape[0]/2
    arcRow=arcData[yIndex]
    bestCorrMax, bestFitScale, bestFitShift=findScaleAndShift(arcRow, refModelDict)
    arc_centreRow=arcRow
    
    # Sanity check plot
    data_x=np.arange(0, arc_centreRow.shape[0])        
    x=np.arange(0, len(arc_centreRow))
    arc_x_shifted=x*(1+bestFitScale)+bestFitShift
    #plt.plot(x, refModelDict['arc_centreRow'][:data_x.shape[0]]/refModelDict['arc_centreRow'][:data_x.shape[0]].mean(), 'b-')
    #plt.plot(arc_x_shifted, arc_centreRow/arc_centreRow.mean(), 'r-')
    #plt.close()
    
    # Tag features by transforming model coords to arc coords and looking for closest match
    # Looking at above sanity plot, seems like the weak link here is centroiding done for 
    # x_centreRow in arcFeatureTable?
    arcFeatureTable.add_column('wavelength', np.zeros(len(arcFeatureTable)))    
    maxDistancePix=20.0
    for row in refModelDict['featureTable']:
        transformed_model_x=(row['x_centreRow']-bestFitShift)/(1+bestFitScale)
        dist=abs(arcFeatureTable['x_centreRow']-transformed_model_x)
        if dist.min() < maxDistancePix:
            index=np.argmin(dist)
            arcFeatureTable['wavelength'][index]=row['wavelength']
    arcFeatureTable=arcFeatureTable.where(arcFeatureTable['wavelength'] != 0)
    if len(arcFeatureTable) == 0:
        raise Exception, "No features identified in arc"    
        
    # Sanity check: tagged features
    #plt.plot(arcData[arcData.shape[0]/2], 'b-')
    #for row in arcFeatureTable:
        #plt.text(row['x_centreRow'], row['amplitude'], row['wavelength'])
    #plt.close()
    
    # Fit wavelength solution on centre row to check order of fit
    # We can't go to higher order if we get something nonsensical (double valued)
    # NOTE: 4th order is worse with spectra we've done before and worked ok...
    #order=4
    #orderOkay=False
    #while orderOkay == False:
        #fitCoeffs=np.polyfit(arcFeatureTable['x_centreRow'], arcFeatureTable['wavelength'], order)
        #poly=np.poly1d(fitCoeffs)
        #try:
            #tck=interpolate.splrep(poly(data_x), data_x)
            #orderOkay=True
        #except:
            #order=order-1
    #diff=arcFeatureTable['wavelength']-poly(arcFeatureTable['x_centreRow'])
    ##plt.plot(arcFeatureTable['x_centreRow'], diff, 'r.')
    #print "... order = %d, median residual = %.3f Angstroms" % (order, np.median(diff))
    #plt.plot(arcFeatureTable['x_centreRow'], arcFeatureTable['wavelength'], 'r.')
    #plt.plot(data_x, poly(data_x), 'k--')
    
    # Find 2d wavelength solution which we can use for rectification/wavelength calibration
    # Fit functions for how feature x positions change with y
    ys=np.arange(arcData.shape[0])
    arcFeatureTable.add_column('slope', np.zeros(len(arcFeatureTable)))
    arcFeatureTable.add_column('intercept', np.zeros(len(arcFeatureTable)))
    for row in arcFeatureTable:
        xs=np.zeros(arcData.shape[0])
        for i in range(len(ys)):
            objPositions=ndimage.center_of_mass(arcData[ys[i]], labels = arcSegMap, index = arcFeatureTable['id'])
            xs[i]=objPositions[np.where(arcFeatureTable['id'] == row['id'])[0]][0]
        # Linear fit should allow us to work out shear - here, given y, we want x
        # We probably don't want all this info (ys, xs), but keep for now
        # We could use polynomial instead of linear (see below)
        try:
            slope, intercept=np.polyfit(ys, xs, 1)
        except:
            print "polyfit failed"
            IPython.embed()
            sys.exit()
        row['slope']=slope
        row['intercept']=intercept

    # Wavelength calibration and model with arbitrary order polynomials - get coeffs for each row
    # We could potentially fit these coeffs as fn. of y - they are all well behaved
    # This array should be all we need to wavelength calibrate + rectify
    wavelengths=arcFeatureTable['wavelength']
    fitCoeffsArr=[]
    for y in range(arcData.shape[0]):
        xs=[]
        for row in arcFeatureTable:
            xs.append(row['slope']*y + row['intercept'])
        xs=np.array(xs)
        try:
            fitCoeffsArr.append(np.polyfit(xs, wavelengths, order))
        except:
            print "WARNING: wavelength calib failed"
            return None
    fitCoeffsArr=np.array(fitCoeffsArr)
    
    # Sanity check: wavelength calibration model with tagged features
    if diagnosticsLabel != None and diagnosticsDir != None:
        yCheck=arcData.shape[0]/2
        wavelengthCalibPoly=np.poly1d(fitCoeffsArr[yCheck])
        wavelengths=wavelengthCalibPoly(np.arange(arcData.shape[1]))
        plt.plot(wavelengths, arcData[arcData.shape[0]/2], 'r-')
        plt.plot(arcFeatureTable['wavelength'], arcFeatureTable['amplitude'], 'bo')
        for row in arcFeatureTable:
            plt.text(row['wavelength'], row['amplitude'], row['wavelength'])
        plt.savefig(diagnosticsDir+os.path.sep+"taggedFeatures_%s.png" % (diagnosticsLabel))
        plt.close()

    # Debugging particular slits
    #if diagnosticsLabel == 'SLIT15':
        #IPython.embed()
        #sys.exit()
    
    return fitCoeffsArr

#-------------------------------------------------------------------------------------------------------------
def wavelengthCalibrateAndRectify(inFileName, outFileName, wavelengthCalibDict, makeDiagnosticPlots = False):
    """Applies the wavelength calibration, and rectification, to all extensions of inFileName, writing 
    output to outFileName. The wavelength calibration is provided in wavelengthCalibDict, where each key
    corresponds to each extension number (see findWavelengthCalibration)
    
    """
    
    print ">>> Applying wavelength calibration and rectifying (%s) ..." % (inFileName)
    img=pyfits.open(inFileName)
    extensionsList=[]
    for hdu in img:
        if "SLIT" in hdu.name:
            extensionsList.append(hdu.name)
                
    for extension in extensionsList:
    
        print "... %s ..." % (extension)
        
        data=img[extension].data
        header=img[extension].header
        fitCoeffsArr=wavelengthCalibDict[extension]
        
        # Can carry on if wavelength calib fails for a slit... fix later...
        if fitCoeffsArr != None:
            
            # Using above, make an array containing wavelengths
            wavelengthsMap=np.zeros(data.shape)
            for y in range(data.shape[0]):
                wavelengthCalibPoly=np.poly1d(fitCoeffsArr[y])
                wavelengthsMap[y]=wavelengthCalibPoly(np.arange(data.shape[1]))
            #astImages.saveFITS("wavelengthsMap.fits", wavelengthsMap, None)
            
            # How we would want our wavelength map to look after applying some transformation
            # To make things easier later, make a linear wavelength scale
            wavelengths_centreRow=wavelengthsMap[wavelengthsMap.shape[0]/2]
            maxWavelength=wavelengths_centreRow.max()
            minWavelength=wavelengths_centreRow.min()
            linearWavelengthRange=np.linspace(minWavelength, maxWavelength, data.shape[1])
            FITSWavelengthScale=linearWavelengthRange[1]-linearWavelengthRange[0]
            FITSRefLambda=linearWavelengthRange[0]
            FITSRefPixel=1                              # Remember index from 1 is FITS convention
            rectWavelengthsMap=np.array([linearWavelengthRange]*data.shape[0])
            #astImages.saveFITS("rectWavelengthsMap.fits", rectWavelengthsMap, None)

            # Remap the data to our preferred linear wavelength scale
            # Assume we can treat each row independently
            # Save linear spectral WCS in header
            rectifiedData=np.zeros(data.shape)
            for y in range(data.shape[0]):
                try:
                    tck=interpolate.splrep(wavelengthsMap[y], data[y])
                except:
                    print "splrep error"
                    IPython.embed()
                    sys.exit()
                rectifiedData[y]=interpolate.splev(rectWavelengthsMap[y], tck, ext = 1)
            img[extension].data=rectifiedData
            header['CTYPE1']='LINEAR'
            header['DISPAXIS']=1
            header['CRVAL1']=FITSRefLambda
            header['CRPIX1']=FITSRefPixel
            header['CD1_1']=FITSWavelengthScale
            header['CDELT1']=FITSWavelengthScale
            header['CUNIT1']='Angstroms'
                
            # Sanity check plot: linear wavelength scale
            if makeDiagnosticPlots == True:
                diagnosticsDir=os.path.split(outFileName)[0]+os.path.sep+"diagnostics"
                if os.path.exists(diagnosticsDir) == False:
                    os.makedirs(diagnosticsDir)
                plt.plot(rectWavelengthsMap[data.shape[0]/2], rectifiedData[data.shape[0]/2], 'k-')
                plt.xlabel("Wavelength (Angstroms)")
                plt.ylabel("Relative Intensity")
                plt.title("%s - %s" % (os.path.split(inFileName)[-1], extension))
                plt.savefig(diagnosticsDir+os.path.sep+"wavelengthCalibCheck_%s_%s.png" % (os.path.split(outFileName)[-1].replace(".fits", ""), extension))
                plt.close()

    # Write output
    img.writeto(outFileName, clobber = True)
    
#-------------------------------------------------------------------------------------------------------------
def wavelengthCalibration2d(maskDict, outDir):
    """Finds 2d wavelength calibration from arc frames, applies to arc frames and object frames, rectifying
    them and also interpolating to a linear wavelength scale to make life easier later.
    
    Should be fully automatic, assuming a suitable reference model is available.
    
    """
       
    diagnosticsDir=outDir+os.path.sep+"diagnostics"
    if os.path.exists(diagnosticsDir) == False:
        os.makedirs(diagnosticsDir)
        
    print ">>> Finding 2d wavelength solution ..."
    maskDict['wavelengthCalib']={}
    for arcFileName in maskDict['ARC']:
        print "--> arc = %s ..." % (arcFileName)
       
        cutArcPath=makeOutputFileName(arcFileName, "c", outDir)                
        img=pyfits.open(cutArcPath)
        
        extensionsList=[]
        for hdu in img:
            if "SLIT" in hdu.name:
                extensionsList.append(hdu.name)

        binning=img[0].header['CCDSUM'].replace(" ", "x")
        grating=img[0].header['GRATING']
        lampid=img[0].header['LAMPID']
        modelFileName=REF_MODEL_DIR+os.path.sep+"RefModel_"+grating+"_"+lampid+"_"+binning+".pickle"

        maskDict['wavelengthCalib'][arcFileName]={}
        for extension in extensionsList:
            print "... extension = %s ..." % (extension)
            arcData=img[extension].data
            if os.path.exists(modelFileName) == False:
                print "No reference model exists for grating %s, lamp %s, with binning %s" % (grating, lampid, binning)
                print "Use createModelArcSpectrum.py script under modelArcSpectra dir"
                print "(arcFileName: %s)" % (arcFileName)
                sys.exit()
            maskDict['wavelengthCalib'][arcFileName][extension]=findWavelengthCalibration(arcData, modelFileName, diagnosticsDir = diagnosticsDir, diagnosticsLabel = extension)
    
    # Apply the calibration to the arc frames (diagnostic purposes)
    # 'rw' prefix => rectified and wavelength calibrated
    for arcFileName in maskDict['ARC']:
        cutArcPath=makeOutputFileName(arcFileName, "c", outDir)                
        rectArcPath=makeOutputFileName(arcFileName, "rwc", outDir)
        wavelengthCalibrateAndRectify(cutArcPath, rectArcPath, maskDict['wavelengthCalib'][arcFileName], makeDiagnosticPlots = True)
    
    # Apply the calibration to object spectra           
    for fileName in maskDict['OBJECT']:
        arcFileName=findMatchingFileByTime(fileName, maskDict['ARC'])
        cutPath=makeOutputFileName(fileName, "c", outDir)
        rectPath=makeOutputFileName(fileName, "rwc", outDir)
        wavelengthCalibrateAndRectify(cutPath, rectPath, maskDict['wavelengthCalib'][arcFileName])        

#-------------------------------------------------------------------------------------------------------------
def weightedExtraction(data, medColumns = 10, thresholdSigma = 30.0, sigmaCut = 3.0, profSigmaPix = 4.0):
    """Extract 1d spectrum of object, sky, and find noisy pixels affected by cosmic rays while we're at it.
    This was (supposed) to be similar to the Horne optimal extraction. We solve:

    ws1*s + k + wn1*n1 = v1
    ws2*s + k + wn2*n2 = v2
    ws3*s + k + wn3*n3 = v3

    |ws1 1 wn1 0   0   | x | s  | = | v1 |
    |ws2 1 0   wn2 0   |   | k  | = | v2 |
    |ws3 1 0   0   wn3 |   | n1 | = | v3 |
                           | n2 |
                           | n3 |

    where ws = signal weight, wk = sky weight == 1, wn = noise weight, s = signal, k = sky, n = noise.

    Sky weight has to be 1, because we have signal + sky everywhere, and the sky level should be the same 
    across all rows. Noise weights are simply 0 or 1 and are used to mask cosmic rays. This seems pretty 
    effective.

    We do all this column by column. 
    
    It turns out this is a great way to get the cosmic rays, but lousy for sky line subtraction.
    
    So, for the actual extraction, we're using masked array median. There is room for improvement here...

    """

    # Throw away rows at edges as these often contain noise
    throwAwayRows=3
    data=data[throwAwayRows:-throwAwayRows]
    
    # Find the chip gaps and make a mask
    lowMaskValue=2.0
    minPix=1000
    chipGapMask=np.array(np.less(data, lowMaskValue), dtype = float)  # flags chip gaps as noise
    segmentationMap, numObjects=ndimage.label(chipGapMask)
    sigPixMask=np.equal(chipGapMask, 1)
    objIDs=np.unique(segmentationMap)
    objNumPix=ndimage.sum(sigPixMask, labels = segmentationMap, index = objIDs)
    for objID, nPix in zip(objIDs, objNumPix):    
        if nPix < minPix:
            chipGapMask[np.equal(segmentationMap, objID)]=0.0

    # All of the below is really just CR rejection now...
    # Assume one object per slit and a fixed width
    traceHalfWidth=4
    prof=np.median(data, axis = 1)
    peakIndex=np.where(prof == prof.max())[0]
    x=np.arange(len(prof))
    xMin=peakIndex-traceHalfWidth
    try:
        if xMin < 0:
            xMin=0
    except:
        print "xMin seems to be array: check peakIndex"
        IPython.embed()
        sys.exit()
    xMax=peakIndex+traceHalfWidth
    if xMax > len(prof)-1:
        xMax=len(prof)-1
    prof[:xMin]=0.0
    prof[xMax:]=0.0    
    
    # Fit/extract signal, sky (but really this is just a good way to find and mask cosmic rays)
    wn2d=np.zeros(data.shape)+chipGapMask               # Treat chip gaps as noise
    ws2d=np.zeros(data.shape)
    wk2d=np.zeros(data.shape)
    recSky=np.zeros(data.shape)
    for k in range(10):
        sky=np.zeros(data.shape[1])
        signal=np.zeros(data.shape[1])
        for i in range(data.shape[1]):
            
            # See testWeightedExtraction2.py, testWeightedExtraction4.py for alternatives using SVD, pseudoInverse etc. 
            v=data[:, i]
            w=np.zeros((v.shape[0], 2+v.shape[0]))
            w[:, 0]=prof.reshape(w[:, 0].shape)     # signal weight - varies across rows
            w[:, 1]=1                               # sky weight - needs to be the same across all rows
            wn=wn2d[:, i]
            for j in range(v.shape[0]):
                w[j, 2+j]=wn[j]                     # noise weights - if 1, zap that pixel (CR or bad)
                # Weights must sum to 1
                if wn[j] == 1.0:
                    w[j, 0]=0.0
                    w[j, 1]=0.0
            ws2d[:, i]=w[:, 0]
            wk2d[:, i]=w[:, 1]
            w=np.matrix(w)
            x, R=optimize.nnls(w, v)
            signal[i]=x[0]
            sky[i]=x[1]

        # Detect noisy pixels
        # Construct a residual image and spot columns/pixels affected by cosmic ray hits etc.
        # Where we have CR hits, have -ve pixels in those columns - ignore those
        # To find, mask -ve in res, find 3sigma clipped median, sigma, and then mask those pixels, then iterate
        sky2d=np.array([sky]*data.shape[0])*wk2d
        signal2d=np.array([signal]*data.shape[0])*ws2d
        res2d=data-sky2d-signal2d
        
        arr=res2d
        thresholdSigma=30.0
        sigmaCut=3.0
        mean=0
        sigma=1e6
        for i in range(20):
            gtrZeroMask=np.greater(arr, lowMaskValue)
            mask=np.less(abs(arr-mean), sigmaCut*sigma)
            mask=np.logical_and(gtrZeroMask, mask)
            mean=np.mean(arr[mask])
            sigma=np.std(arr[mask])
        detectionThreshold=thresholdSigma*sigma
        wn2d=np.array(np.greater(arr-mean, detectionThreshold), dtype = float)
        wn2d[np.less(arr, 0)]=0.0
        
        # Insert code (perhaps) to grow mask around CR hits (code is in testWeightedExtraction4)
        # However, experiments showed this sometimes made things worse - fix later...
        
        # Add in the mask for chip gaps
        wn2d=wn2d+chipGapMask
        wn2d[np.greater(wn2d, 1)]=1.0
        
        # Now subtract sky model and re-estimate spatial profile
        # NOTE: This seems to make cosmic rays have bigger impact
        #prof=np.median(data-sky2d, axis = 1)
        #prof=prof/prof.max()
        #x0=np.where(prof == prof.max())[0][0]
        #x=np.arange(prof.shape[0])
        #sigmaRange=np.linspace(1, prof.shape[0]/2, prof.shape[0]*2)
        #resArr=np.zeros(sigmaRange.shape[0])
        #for i in range(len(sigmaRange)):
            #s=sigmaRange[i]
            #gauss=np.exp(-((x-x0)**2)/(2*s**2))
            #resArr[i]=abs(prof-gauss).sum()
            ##plt.plot(gauss)
        #sigma=sigmaRange[np.where(resArr == resArr.min())]/2
        #prof=np.exp(-((x-x0)**2)/(2*sigma**2))

    # Use masked array median - this actually seems to work better than what we were doing...
    # NOTE: mask values of 1 are excluded, 0 included
    # Room for improvement here (e.g., optimize traceHalfWidth for each object)
    traceHalfWidth=4
    prof=np.median(data, axis = 1)
    peakIndex=np.where(prof == prof.max())[0]
    x=np.arange(len(prof))
    xMin=peakIndex-traceHalfWidth
    if xMin < 0:
        xMin=0
    xMax=peakIndex+traceHalfWidth
    if xMax > len(prof)-1:
        xMax=len(prof)-1
    prof[:xMin]=0.0
    prof[xMax:]=0.0
    
    skyMask=np.array([np.greater(prof, 0)]*data.shape[1], dtype = int).transpose()
    signalMask=np.array([np.equal(prof, 0)]*data.shape[1], dtype = int).transpose()
    mSky2d=np.ma.masked_array(data, skyMask+wn2d)
    mSignalPlusSky2d=np.ma.masked_array(data, signalMask+wn2d)
    sky=np.ma.median(mSky2d, axis = 0)
    signalPlusSky=np.ma.median(mSignalPlusSky2d, axis = 0)
    
    signal=signalPlusSky-sky
    
    # Biweight
    #bsSky=[]
    #bsSignalPlusSky=[]
    #for i in range(data.shape[1]):
        #bsSky.append(astStats.biweightLocation(data[:, i][np.where(skyMask[:, i] == 0)], 9))
        #bsSignalPlusSky.append(astStats.biweightLocation(data[:, i][np.where(signalMask[:, i] == 0)], 9)) 
    #bsSky=np.array(bsSky, dtype = float)
    #bsSky[np.isnan(bsSky)]=0.0
    #bsSignalPlusSky=np.array(bsSignalPlusSky, dtype = float)
    #bsSignalPlusSky[np.isnan(bsSignalPlusSky)]=0.0
    #bsSignal=bsSignalPlusSky-bsSky
    
    return signal, sky

#-------------------------------------------------------------------------------------------------------------
def extractAndStackSpectra(maskDict, outDir):
    """Extracts and stacks spectra from science frames which have already been wavelength calibrated.
    
    Output is in .fits table format.
        
    """
    
    onedspecDir=outDir+os.path.sep+"1DSpec"
    if os.path.exists(onedspecDir) == False:
        os.makedirs(onedspecDir)
    
    # Get list of extensions
    arcFileName=maskDict['ARC'][0]    
    cutArcPath=makeOutputFileName(arcFileName, "rwc", outDir)
    img=pyfits.open(cutArcPath)
    extensionsList=[]
    for hdu in img:
        if "SLIT" in hdu.name:
            extensionsList.append(hdu.name)
    
    # The way we stack... identify signal dominated rows and average them to a 1d spectrum, then stack all 1d
    # Do same for sky rows
    # They may all be projected onto slightly different wavelength coordinates system... have to deal with that also
    toStackList=makeOutputFileNameList(maskDict['OBJECT'], 'rwc', outDir)
    print ">>> Extracting and stacking..."
    for extension in extensionsList:
        print "... %s ..." % (extension)
        signalList=[]
        skyList=[]
        wavelengthsList=[]
        for fileName in toStackList:
            
            img=pyfits.open(fileName)
            data=img[extension].data
            header=img[extension].header
            
            # Extract calibrated wavelength scale, assuming left most pixel corresponds to CRVAL1
            w=np.arange(data.shape[1])*header['CDELT1']+header['CRVAL1']
            if w[0] != header['CRVAL1']:
                raise Exception, "wavelength of pixel 0 doesn't correspond to CRVAL1 - what happened?"
            wavelengthsList.append(w)
            
            # Extract signal, sky
            signal, sky=weightedExtraction(data)
            #if extension == 'SLIT9':    # SLIT15 for BCG
                #IPython.embed()
                #sys.exit()
            signalList.append(signal)
            skyList.append(sky)
        
        # Make stacked spectrum - interpolate onto common wavelength scale, then take median
        # We could make this fancier (noise weighting etc.)...
        signalArr=np.array(signalList)
        skyArr=np.array(skyList)
        wavelengthsArr=np.array(wavelengthsList)
        wavelength=np.median(wavelengthsArr, axis = 0)
        regrid_signalArr=np.zeros(signalArr.shape)
        regrid_skyArr=np.zeros(skyArr.shape)
        for i in range(signalArr.shape[0]):
            tck=interpolate.splrep(wavelengthsArr[i], signalArr[i])
            regrid_signalArr[i]=interpolate.splev(wavelength, tck)
            tck=interpolate.splrep(wavelengthsArr[i], skyArr[i])
            regrid_skyArr[i]=interpolate.splev(wavelength, tck)            
        signal=np.median(regrid_signalArr, axis = 0)
        sky=np.median(regrid_skyArr, axis = 0)
        
        # Output as .fits tables, one per slit
        specColumn=pyfits.Column(name='SPEC', format='D', array=signal)
        skyColumn=pyfits.Column(name='SKYSPEC', format='D', array=sky)
        lambdaColumn=pyfits.Column(name='LAMBDA', format='D', array=wavelength)
        tabHDU=pyfits.new_table([specColumn, skyColumn, lambdaColumn])
        tabHDU.name='1D_SPECTRUM'
        HDUList=pyfits.HDUList([pyfits.PrimaryHDU(), tabHDU])
        outFileName=onedspecDir+os.path.sep+maskDict['maskName']+"_"+maskDict['objectName'].replace(" ", "_")+"_"+extension+".fits"
        HDUList.writeto(outFileName, clobber=True)
   
#-------------------------------------------------------------------------------------------------------------
# Main
if len(sys.argv) < 4:
    print "Run: % rss_mos_reducer.py rawDir reducedDir maskName"
else:

    # There will be a UI ultimately
    rawDir=sys.argv[1]
    baseOutDir=sys.argv[2].replace("\n", "")

    if os.path.exists(baseOutDir) == False:
        os.makedirs(baseOutDir)
    
    # Sort out what's what...
    infoDict=getImageInfo(rawDir)
    
    maskID=sys.argv[3]
    shortDict={}
    for key in infoDict.keys():
        if key == maskID:
            shortDict[key]=infoDict[key]
    infoDict=shortDict
    
    # We're organised by the mask, reduce each in turn
    for maskID in infoDict.keys():  # try more complicated case first
        
        print ">>> Mask: %s" % (maskID)
        
        outDir=baseOutDir+os.path.sep+maskID
        if os.path.exists(outDir) == False:
            os.makedirs(outDir)
            
        maskDict=infoDict[maskID]
        
        makeMasterFlats(maskDict, outDir)

        cutIntoSlitLets(maskDict, outDir)
        
        applyFlatField(maskDict, outDir)
        
        wavelengthCalibration2d(maskDict, outDir)

        extractAndStackSpectra(maskDict, outDir)
 
    
    
