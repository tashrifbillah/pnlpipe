#!/usr/bin/env python
from plumbum import local, FG, cli, ProcessExecutionError
from pnlscripts.util.scripts import dwiconvert_py, atlas_py, fs2dwi_py, eddy_py, alignAndCenter_py, bet_py
from pnlscripts.util import TemporaryDirectory
import sys
import hashlib
import pnlpipe_lib
from pnlpipe_lib import InputKey, GeneratedNode, need, needDeps, log
from pnlpipe_software import BRAINSTools, tract_querier, UKFTractography, trainingDataT1AHCC, HCPPipelines
import pnlpipe_software.FreeSurfer
import pnlpipe_config

defaultUkfParams = ["--Ql", 70, "--Qm", 0.001, "--Rs", 0.015, "--numTensor", 2,
                    "--recordLength", 1.7, "--seedFALimit", 0.18,
                    "--seedsPerVoxel", 10, "--stepLength", 0.3]

OUTDIR = local.path(pnlpipe_config.OUTDIR)


class DoesNotExistException(Exception):
    pass


def assertInputKeys(pipelineName, keys):
    import pp
    absentKeys = [k for k in keys if not pp.INPUT_PATHS.get(k)]
    if absentKeys:
        for key in absentKeys:
            print("{} requires '{}' set in inputPaths.yml".format(pipelineName,
                                                                  key))
        sys.exit(1)


def read_csv(file):
    import pandas as pd
    try:
        df = pd.read_csv(file)
    except:
        df = None
    return df


def tractMeasureStatus(combos, extraFlags=[]):
    import pandas as pd
    dfs = []
    for combo in combos:
        csvs = [p.path for p in combo['paths']['tractmeasures']
                if p.path.exists()]
        if csvs:
            df = pd.concat(
                filter(lambda x: x is not None, (read_csv(csv)
                                                 for csv in csvs)))
            df['algo'] = combo['paramId']
            dfs.append(df)
    if dfs:
        from pnlpipe_pipelines.pnlscripts.summarizeTractMeasures import summarize
        df = pd.concat(dfs)
        df_summary = summarize(df)
        #if 'csv' in extraFlags:
        outcsv = OUTDIR / (combos[0]['pipelineName'] + '-tractmeasures.csv')
        df.to_csv(outcsv.__str__(), header=True, index=False)
        print("Made '{}'".format(outcsv))
        outcsv_summary = OUTDIR / (
            combos[0]['pipelineName'] + '-tractmeasures-summary.csv')
        #df_summary.to_csv(outcsv_summary.__str__(), header=True, index=False)
        df_summary.to_csv(outcsv_summary.__str__(), header=True)
        print("Made '{}'".format(outcsv_summary))


def convertImage(i, o, bthash):
    if i.suffixes == o.suffixes:
        i.copy(o)
    with BRAINSTools.env(bthash):
        from plumbum.cmd import ConvertBetweenFileFormats
        ConvertBetweenFileFormats(i, o)


def formatParams(l):
    formatted = [['--' + key, val] for key, val in dic.items()]
    return [item for pair in formatted for item in pair if item]


def validateFreeSurfer(versionRequired):
    freesurferHome = os.environ.get('FREESURFER_HOME')
    if not freesurferHome:
        log.error(
            "'FREESURFER_HOME' not set, set that first (need version {}) then run again".format(
                version))
        sys.exit(1)
    with open(local.path(freesurferHome) / "build-stamp.txt", 'r') as f:
        buildStamp = f.read()
    import re
    p = re.compile('v\d\.\d\.\d(-\w+)?$')
    try:
        version = p.search(buildStamp).group()
    except:
        log.error(
            "Couldn't extract FreeSurfer version from {}/build-stamp.txt, either that file is malformed or the regex used to extract the version is incorrect.".format(
                freesurferHome))

        sys.exit(1)
    if version == versionRequired:
        log.info("Required FreeSurfer version {} is on path".format(version))
    else:
        log.error(
            "FreeSurfer version {} at {} does not match the required version of {}, either change FREESURFER_HOME or change the version you require".format(
                version, freesurferHome, versionRequired))
        sys.exit(1)


class DwiHcp(GeneratedNode):
    """ Washington University HCP DWI preprocessing. """

    def __init__(self, caseid, posDwis, negDwis, echoSpacing, peDir,
                 version_HCPPipelines):
        self.deps = posDwis + negDwis
        self.params = [version_HCPPipelines]
        self.ext = '.nii.gz'
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with HCPPipelines.env(self.version_HCPPipelines), TemporaryDirectory(
        ) as tmpdir:
            preproc = local[HCPPipelines.get_path(self.version_HCPPipelines) /
                            'DiffusionPreprocessing/DiffPreprocPipeline.sh']
            posPaths = [n.output() for n in self.posDwis]
            negPaths = [n.output() for n in self.negDwis]
            datadir = tmpdir / 'hcp/data'
            from os import getpid
            hcpdir = OUTDIR / self.caseid / 'hcp-{}'.format(getpid())
            datadir = hcpdir / 'data'
            try:
                preproc['--path={}'.format(OUTDIR), '--subject={}'.format(
                    self.caseid), '--PEdir={}'.format(self.peDir), '--posData='
                        + '@'.join(posPaths), '--negData=' + '@'.join(
                            negPaths), '--echospacing={}'.format(
                                self.echoSpacing), '--gdcoeffs=NONE',
                        '--dwiname=hcp-{}'.format(getpid())] & FG
            except ProcessExecutionError as e:
                if not (datadir / 'data.nii.gz').exists():
                    print(e)
                    log.error("HCP failed to make '{}'".format(datadir /
                                                               'data.nii.gz'))
                    (OUTDIR / self.caseid / 'T1w').delete()
                    sys.exit(1)
            (OUTDIR / self.caseid / 'T1w').delete()
            (datadir / 'data.nii.gz').move(self.output())
            (datadir / 'bvals').move(self.output().with_suffix(
                '.bval', depth=2))
            (datadir / 'bvecs').move(self.output().with_suffix(
                '.bvec', depth=2))


class DwiEd(GeneratedNode):
    """ Eddy current correction. Accepts nrrd only. """

    def __init__(self, caseid, dwi, bthash):
        self.deps = [dwi]
        self.params = [bthash]
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with BRAINSTools.env(self.bthash):
            eddy_py['-i', self.dwi.output(), '-o', self.output(),
                    '--force'] & FG


class DwiXc(GeneratedNode):
    """ Axis align and center a DWI. Accepts nrrd or nifti. """

    def __init__(self, caseid, dwi, bthash):
        self.deps = [dwi]
        self.params = [bthash]
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with BRAINSTools.env(self.bthash), TemporaryDirectory() as tmpdir:
            tmpdwi = tmpdir / (self.caseid + '-dwi.nrrd')
            dwiconvert_py['-f', '-i', self.dwi.output(), '-o', tmpdwi] & FG
            alignAndCenter_py['-i', tmpdwi, '-o', self.output()] & FG


class DwiEpi(GeneratedNode):
    """Epi correction. """

    def __init__(self, caseid, dwi, dwimask, t2, t2mask, bthash):
        self.deps = [dwi, dwimask, t2, t2mask]
        self.params = [bthash]
        GeneratedNode.__init__(self, locals())

    def build(self):
        needDeps(self)
        with BRAINSTools.env(self.bthash):
            from pnlscripts.util.scripts import epi_py
            epi_py('--force', '--dwi', self.dwi.output(), '--dwimask',
                   self.dwimask.output(), '--t2', self.t2.output(), '--t2mask',
                   self.t2mask.output(), '-o', self.output())


class DwiMaskBet(GeneratedNode):
    def __init__(self, caseid, dwi, threshold, bthash):
        self.deps = [dwi]
        self.params = [threshold, bthash]
        GeneratedNode.__init__(self, locals())

    def build(self):
        needDeps(self)
        with BRAINSTools.env(self.bthash), TemporaryDirectory() as tmpdir:
            bet_py('--force', '-f', self.threshold, '-i', self.dwi.output(),
                   '-o', self.output())


class UkfDefault(GeneratedNode):
    def __init__(self, caseid, dwi, dwimask, ukfhash, bthash):
        self.deps = [dwi, dwimask]
        self.params = [ukfhash, bthash]
        self.ext = 'vtk'
        GeneratedNode.__init__(self, locals())

    def build(self):
        needDeps(self)
        with BRAINSTools.env(self.bthash), TemporaryDirectory() as tmpdir:
            tmpdir = local.path(tmpdir)
            tmpdwi = tmpdir / 'dwi.nrrd'
            tmpdwimask = tmpdir / 'dwimask.nrrd'
            dwiconvert_py('-i', self.dwi.output(), '-o', tmpdwi)
            convertImage(self.dwimask.output(), tmpdwimask, self.bthash)
            params = ['--dwiFile', tmpdwi, '--maskFile', tmpdwimask,
                      '--seedsFile', tmpdwimask, '--recordTensors', '--tracts',
                      self.output()] + defaultUkfParams
            ukfpath = UKFTractography.get_path(self.ukfhash)
            log.info(' Found UKF at {}'.format(ukfpath))
            ukfbin = local[ukfpath]
            ukfbin(*params)


class Ukf(GeneratedNode):
    def __init__(self, caseid, dwi, dwimask, ukfparams, ukfhash, bthash):
        self.deps = [dwi, dwimask]
        ukfparamsHash = "ukfparams-" + str(
            int(hashlib.sha1(ukfparams.__str__()).hexdigest(), 16) % (10**8))
        self.params = [ukfhash, bthash, ukfparamsHash]
        self.ext = '.vtk'
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with BRAINSTools.env(self.bthash), TemporaryDirectory() as tmpdir:
            tmpdir = local.path(tmpdir)
            tmpdwi = tmpdir / 'dwi.nrrd'
            tmpdwimask = tmpdir / 'dwimask.nrrd'
            dwiconvert_py('-i', self.dwi.output(), '-o', tmpdwi)
            convertImage(self.dwimask.output(), tmpdwimask, self.bthash)
            params = ['--dwiFile', tmpdwi, '--maskFile', tmpdwimask,
                      '--seedsFile', tmpdwimask, '--recordTensors', '--tracts',
                      self.output()] + list(self.ukfparams)
            ukfpath = UKFTractography.get_path(self.ukfhash)
            log.info(' Found UKF at {}'.format(ukfpath))
            ukfbin = local[ukfpath]
            # ukfbin(*params)
            ukfbin.bound_command(*params) & FG


class StrctXc(GeneratedNode):
    def __init__(self, caseid, strct, bthash):
        self.deps = [strct]
        self.params = [bthash]
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with BRAINSTools.env(self.bthash), TemporaryDirectory() as tmpdir:
            nrrd = tmpdir / 'strct.nrrd'
            convertImage(self.strct.output(), nrrd, self.bthash)
            alignAndCenter_py['-i', nrrd, '-o', self.output()] & FG


class MaskRigid(GeneratedNode):
    def __init__(self, caseid, fixedStrct, movingStrct, movingStrctMask,
                 bthash):
        self.deps = [fixedStrct, movingStrct, movingStrctMask]
        self.params = [bthash]
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with BRAINSTools.env(self.bthash), TemporaryDirectory() as tmpdir:
            from pnlscripts.util.scripts import makeRigidMask_py
            moving = tmpdir / 'moving.nrrd'
            movingmask = tmpdir / 'movingmask.nrrd'
            fixed = tmpdir / 'fixed.nrrd'
            out = tmpdir / 'fixedmask.nrrd'
            convertImage(self.movingStrct.output(), moving, self.bthash)
            convertImage(self.movingStrctMask.output(), movingmask,
                         self.bthash)
            convertImage(self.fixedStrct.output(), fixed, self.bthash)
            makeRigidMask_py('-i', moving, '--labelmap', movingmask,
                             '--target', fixed, '-o', out)
            out.move(self.output())


class T1wMaskMabs(GeneratedNode):
    def __init__(self, caseid, t1, trainingDataT1AHCC, bthash):
        self.deps = [t1]
        self.params = [bthash]
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        with TemporaryDirectory() as tmpdir, BRAINSTools.env(self.bthash):
            tmpdir = local.path(tmpdir)
            # antsRegistration can't handle a non-conventionally named file, so
            # we need to pass in a conventionally named one
            tmpt1 = tmpdir / ('t1' + ''.join(self.t1.output().suffixes))
            from plumbum.cmd import ConvertBetweenFileFormats
            ConvertBetweenFileFormats[self.t1.output(), tmpt1] & FG
            trainingCsv = trainingDataT1AHCC.get_path(
                self.trainingDataT1AHCC) / 'trainingDataT1AHCC-hdr.csv'
            atlas_py['csv', '--fusion', 'avg', '-t', tmpt1, '-o', tmpdir,
                     trainingCsv] & FG
            (tmpdir / 'mask.nrrd').copy(self.output())


class FreeSurferUsingMask(GeneratedNode):
    def __init__(self, caseid, t1, t1mask, version_FreeSurfer):
        self.deps = [t1, t1mask]
        self.params = [version_FreeSurfer]
        GeneratedNode.__init__(self, locals())

    def path(self):
        return OUTDIR / self.caseid / showDAG(self) / 'mri/wmparc.mgz'

    def build(self, db):
        needDeps(self, self.deps, db)
        # make sure FREESURFER_HOME is set to right version
        pnlpipe_software.FreeSurfer.validate(self.version_FreeSurfer)
        from pnlscripts.util.scripts import fs_py
        fs_py['-i', self.t1.output(), '-m', self.t1mask.output(), '-f', '-o',
              self.output().dirname.dirname] & FG


class FsInDwiDirect(GeneratedNode):
    def __init__(self, caseid, fs, dwi, dwimask, bthash):
        self.deps = [fs, dwi, dwimask]
        self.params = [bthash]
        self.ext = 'nii.gz'
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        fssubjdir = self.fs.output().dirname.dirname
        with TemporaryDirectory() as tmpdir, BRAINSTools.env(self.bthash):
            tmpoutdir = tmpdir / (self.caseid + '-fsindwi')
            tmpdwi = tmpdir / 'dwi.nrrd'
            tmpdwimask = tmpdir / 'dwimask.nrrd'
            dwiconvert_py('-i', self.dwi.output(), '-o', tmpdwi)
            convertImage(self.dwimask.output(), tmpdwimask, self.bthash)
            fs2dwi_py['-f', fssubjdir, '-t', tmpdwi, '-m', tmpdwimask, '-o',
                      tmpoutdir, 'direct'] & FG
            local.path(tmpoutdir / 'wmparcInDwi1mm.nii.gz').copy(self.output())


class FsInDwiUsingT2(GeneratedNode):
    def __init__(self, caseid, fs, t1, t1mask, t2, t2mask, dwi, dwimask,
                 bthash):
        self.deps = [fs, t1, t2, t1mask, t2mask, dwi, dwimask]
        self.params = [bthash]
        self.ext = 'nii.gz'
        GeneratedNode.__init__(self, locals())

    def build(self):
        needDeps(self)
        fssubjdir = self.fs.output().dirname.dirname
        with TemporaryDirectory() as tmpdir, BRAINSTools.env(self.bthash):
            tmpoutdir = tmpdir / (self.caseid + '-fsindwi')
            dwi = tmpdir / 'dwi.nrrd'
            dwimask = tmpdir / 'dwimask.nrrd'
            fs = tmpdir / 'fs'
            t2 = tmpdir / 't2.nrrd'
            t1 = tmpdir / 't1.nrrd'
            t1mask = tmpdir / 't1mask.nrrd'
            t2mask = tmpdir / 't2mask.nrrd'
            fssubjdir.copy(fs)
            dwiconvert_py('-i', self.dwi.output(), '-o', dwi)
            convertImage(self.dwimask.output(), dwimask, self.bthash)
            convertImage(self.t2.output(), t2, self.bthash)
            convertImage(self.t1.output(), t1, self.bthash)
            convertImage(self.t2mask.output(), t2mask, self.bthash)
            convertImage(self.t1mask.output(), t1mask, self.bthash)
            script = local['pnlpipe_pipelines/pnlscripts/old/fs2dwi_T2.sh']
            script['--fsdir', fs, '--dwi', dwi, '--dwimask', dwimask, '--t2',
                   t2, '--t2mask', t2mask, '--t1', t1, '--t1mask', t1mask,
                   '-o', tmpoutdir] & FG
            convertImage(tmpoutdir / 'wmparc-in-bse.nrrd', self.output(),
                         self.bthash)


class Wmql(GeneratedNode):
    def __init__(self, caseid, fsindwi, ukf, tqhash):
        self.deps = [fsindwi, ukf]
        self.params = [tqhash]
        GeneratedNode.__init__(self, locals())

    def path(self):
        return OUTDIR / self.caseid / self.showCompressedDAG() / 'cc.vtk'

    def build(self, db):
        needDeps(self, self.deps, db)
        if self.output().up().exists():
            self.output().up().delete()
        with tract_querier.env(self.tqhash):
            from pnlscripts.util.scripts import wmql_py
            wmql_py['-i', self.ukf.output(), '--fsindwi', self.fsindwi.output(
            ), '-o', self.output().dirname] & FG


class TractMeasures(GeneratedNode):
    def __init__(self, caseid, wmql):
        self.deps = [wmql]
        self.ext = 'csv'
        GeneratedNode.__init__(self, locals())

    def build(self, db):
        needDeps(self, self.deps, db)
        measureTracts_py = local[
            'pnlpipe_pipelines/pnlscripts/measuretracts/measureTracts.py']
        vtks = self.wmql.output().up() // '*.vtk'
        measureTracts_py['-f', '-c', 'caseid', 'algo', '-v', self.caseid,
                         self.wmql.showCompressedDAG(), '-o', self.output(
                         ), '-i', vtks] & FG
