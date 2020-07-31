import os
from os.path import join as opj
from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu
from .preprocess_anat_pkg.anat_preproc import init_anat_preproc_wf
from .preprocess_bold_pkg.bold_main_wf import init_bold_main_wf, commonspace_reg_function
from .preprocess_bold_pkg.registration import run_antsRegistration
from .preprocess_bold_pkg.utils import BIDSDataGraber, prep_bids_iter, convert_to_RAS
from .QC_report import PlotOverlap, PlotMotionTrace
from .conf_reg.confound_regression import init_confound_regression_wf
from nipype.interfaces.io import SelectFiles, DataSink

from nipype.interfaces.utility import Function

def init_unified_main_wf(data_dir_path, output_folder, disable_anat_preproc=False, autoreg=False, apply_despiking=False, tr='1.0s', tpattern='altplus', apply_STC=True, detect_dummy=False, slice_mc=False, template_reg_script=None,
                bias_reg_script='Rigid', coreg_script='SyN', nativespace_resampling='origin', commonspace_resampling='origin', CR_meta={'apply_CR':False}, name='main_wf'):
    '''
    This workflow includes complete anatomical and BOLD preprocessing within a single workflow.

    **Parameters**

        data_dir_path
            Path to the input data directory with proper BIDS folder structure.
        output_folder
            path to output folder for the workflow and datasink
        apply_despiking
            whether to apply despiking using AFNI's 3dDespike https://afni.nimh.nih.gov/pub/dist/doc/program_help/3dDespike.html.
        tr
            repetition time for the EPI
        tpattern
            specification for the within TR slice acquisition method. The input is fed to AFNI's 3dTshift
        apply_STC
            whether to apply slice timing correction (STC) or not
        detect_dummy
            whether to detect and remove dummy volumes at the beginning of the EPI Sequences
        slice_mc
            whether to apply slice-specific motion correction through 2D registration of each slice, which can improve the correction
            of within-TR motion
        template_reg_script
            registration script for the registration of the dataset template to the commonspace template
        bias_reg_script
            path to registration script that will be applied for bias field correction. The script must
            follow the template structure of registration scripts in shell_scripts/.
            Default is set to 'Rigid' registration.
        coreg_script
            path to registration script for EPI to anat coregistraion. The script must
            follow the template structure of registration scripts in shell_scripts/.
            Default is set to 'SyN' registration.
        nativespace_resampling
            Specified dimensions for the resampling of the corrected EPI in native space.
        commonspace_resampling
            Specified dimensions for the resampling of the corrected EPI in common space.

    **Outputs**

        anat_preproc
            Preprocessed anatomical image after bias field correction and denoising
        anat_mask
            Brain mask inherited from the common space registration
        anat_labels
            Anatomical labels inherited from the common space registration
        WM_mask
            Eroded WM mask inherited from the common space registration
        CSF_mask
            Eroded CSF mask inherited from the common space registration
        initial_bold_ref
            Initial EPI median volume subsequently used as 3D reference EPI volume
        bias_cor_bold
            3D reference EPI volume after bias field correction
        itk_bold_to_anat
            Composite transforms from the EPI space to the anatomical space
        itk_anat_to_bold
            Composite transforms from the anatomical space to the EPI space
        bias_cor_bold_warped2anat
            Bias field corrected 3D EPI volume warped to the anatomical space
        native_corrected_bold
            Original BOLD timeseries resampled through motion realignment and
            susceptibility distortion correction based on registration to the
            anatomical image
        corrected_bold_ref
            3D median EPI volume from the resampled native BOLD timeseries
        confounds_csv
            .csv file with measured confound timecourses, including global signal,
            WM signal, CSF signal, 6 rigid body motion parameters + their first
            temporal derivate + the 12 parameters squared (24 motion parameters),
            and aCompCorr timecourses
        FD_voxelwise
            Voxelwise framewise displacement (FD) measures that can be integrated
            to future confound regression.
            These measures are computed from antsMotionCorrStats.
        pos_voxelwise
            Voxel distancing across time based on rigid body movement parameters,
            which can be integrated for a voxelwise motion regression
            These measures are computed from antsMotionCorrStats.
        FD_csv
            .csv file with global framewise displacement (FD) measures
        bold_brain_mask
            EPI brain mask for native corrected bold
        bold_WM_mask
            EPI WM mask for native corrected bold
        bold_CSF_mask
            EPI CSF mask for native corrected bold
        bold_labels
            EPI anatomical labels for native corrected bold
        commonspace_bold
            Motion and SDC-corrected EPI timeseries resampled into common space
            by applying transforms from the anatomical common space registration
        commonspace_mask
            EPI brain mask for commonspace bold
        commonspace_WM_mask
            EPI WM mask for commonspace bold
        commonspace_CSF_mask
            EPI CSF mask for commonspace bold
        commonspace_labels
            EPI anatomical labels for commonspace bold
    '''

    workflow = pe.Workflow(name=name)

    #set output node
    outputnode = pe.Node(niu.IdentityInterface(
        fields=['anat_preproc','anat_mask', 'anat_labels', 'WM_mask', 'CSF_mask','initial_bold_ref', 'bias_cor_bold', 'affine_bold2anat', 'warp_bold2anat', 'inverse_warp_bold2anat', 'bias_cor_bold_warped2anat', 'native_corrected_bold', 'corrected_bold_ref', 'confounds_csv','FD_voxelwise', 'pos_voxelwise', 'FD_csv',
                'bold_brain_mask', 'bold_WM_mask', 'bold_CSF_mask', 'bold_labels', 'commonspace_bold', 'commonspace_mask', 'commonspace_WM_mask', 'commonspace_CSF_mask', 'commonspace_labels']),
        name='outputnode')

    from bids.layout import BIDSLayout
    layout = BIDSLayout(data_dir_path, validate=False)
    subject_list, session_iter, run_iter=prep_bids_iter(layout)
    #set SelectFiles nodes
    anat_selectfiles = pe.Node(BIDSDataGraber(bids_dir=data_dir_path, datatype='anat'), name='anat_selectfiles')
    bold_selectfiles = pe.Node(BIDSDataGraber(bids_dir=data_dir_path, datatype='func'), name='bold_selectfiles')

    ####setting up all iterables
    infosub_id = pe.Node(niu.IdentityInterface(fields=['subject_id']),
                      name="infosub_id")
    infosub_id.iterables = [('subject_id', subject_list)]

    infosession = pe.Node(niu.IdentityInterface(fields=['session', 'subject_id']),
                      name="infosession")
    infosession.itersource = ('infosub_id', 'subject_id')
    infosession.iterables = [('session', session_iter)]

    inforun = pe.Node(niu.IdentityInterface(fields=['run', 'subject_id']),
                      name="inforun")
    inforun.itersource = ('infosub_id', 'subject_id')
    inforun.iterables = [('run', run_iter)]

    # Datasink - creates output folder for important outputs
    anat_datasink = pe.Node(DataSink(base_directory=output_folder,
                             container="anat_datasink"),
                    name="anat_datasink")

    bold_datasink = pe.Node(DataSink(base_directory=output_folder,
                             container="bold_datasink"),
                    name="bold_datasink")

    commonspace_datasink = pe.Node(DataSink(base_directory=output_folder,
                             container="commonspace_datasink"),
                    name="commonspace_datasink")

    transforms_datasink = pe.Node(DataSink(base_directory=output_folder,
                             container="transforms_datasink"),
                    name="transforms_datasink")

    confounds_datasink = pe.Node(DataSink(base_directory=output_folder,
                             container="confounds_datasink"),
                    name="confounds_datasink")

    #####setting up commonspace registration within the workflow
    joinnode_session = pe.JoinNode(niu.IdentityInterface(fields=['file_list']),
                     name='joinnode_session',
                     joinsource='infosession',
                     joinfield=['file_list'])

    joinnode_sub_id = pe.JoinNode(niu.IdentityInterface(fields=['file_list']),
                     name='joinnode_sub_id',
                     joinsource='infosub_id',
                     joinfield=['file_list'])


    #connect iterables, joinnodes and SelectFiles
    workflow.connect([
        (infosub_id, infosession, [
            ("subject_id", "subject_id"),
            ]),
        (infosub_id, inforun, [
            ("subject_id", "subject_id"),
            ]),
        (infosub_id, anat_selectfiles, [
            ("subject_id", "subject_id"),
            ]),
        (infosession, anat_selectfiles, [
            ("session", "session")
            ]),
        (joinnode_session, joinnode_sub_id, [
            ("file_list", "file_list"),
            ]),
        (infosub_id, bold_selectfiles, [
            ("subject_id", "subject_id"),
            ]),
        (infosession, bold_selectfiles, [
            ("session", "session")
            ]),
        (inforun, bold_selectfiles, [
            ("run", "run")
            ]),
        ])

    #node to conver input image to consistent RAS orientation
    anat_convert_to_RAS_node = pe.Node(Function(input_names=['img_file'],
                              output_names=['RAS_file'],
                              function=convert_to_RAS),
                     name='anat_convert_to_RAS')
    bold_convert_to_RAS_node = pe.Node(Function(input_names=['img_file'],
                              output_names=['RAS_file'],
                              function=convert_to_RAS),
                     name='bold_convert_to_RAS')

    #Resample the anatomical template according to the resolution of the provided input data
    layout = BIDSLayout(data_dir_path, validate=False)
    anat_file_list=layout.get(extension=['nii', 'nii.gz'], datatype='anat', return_type='filename')

    from rabies.preprocess_bold_pkg.utils import resample_template
    resample_template_node = pe.Node(Function(input_names=['template_file', 'file_list', 'spacing'],
                              output_names=['resampled_template'],
                              function=resample_template),
                     name='resample_template', mem_gb=1*float(os.environ["rabies_mem_scale"]))
    resample_template_node.inputs.file_list=anat_file_list
    resample_template_node.inputs.template_file=os.environ["template_anat"]
    resample_template_node.inputs.spacing=os.environ["anatomical_resampling"]

    #setting anat preprocessing nodes
    anat_preproc_wf = init_anat_preproc_wf(disable_anat_preproc=disable_anat_preproc,autoreg=autoreg)

    #calculate the number of anat scans that will be registered
    num_anat=0
    for sub in subject_list:
        num_anat+=len(session_iter[sub])
    if int(os.environ["local_threads"])<num_anat:
        num_anat=int(os.environ["local_threads"])

    commonspace_reg = pe.Node(Function(input_names=['file_list', 'template_anat', 'output_folder'],
                              output_names=['ants_dbm_template'],
                              function=commonspace_reg_function),
                     name='commonspace_reg', n_procs=num_anat, mem_gb=1*num_anat*float(os.environ["rabies_mem_scale"]))
    commonspace_reg.inputs.output_folder = output_folder+'/commonspace_datasink/'

    #execute the registration of the generate anatomical template with the provided atlas for labeling and masking
    template_reg = pe.Node(Function(input_names=['reg_script', 'moving_image', 'fixed_image', 'anat_mask'],
                              output_names=['affine', 'warp', 'inverse_warp', 'warped_image'],
                              function=run_antsRegistration),
                     name='template_reg', mem_gb=2*float(os.environ["rabies_mem_scale"]))
    template_reg.plugin_args = {'qsub_args': '-pe smp %s' % (str(3*int(os.environ["min_proc"]))), 'overwrite': True}
    template_reg.inputs.anat_mask = os.environ["template_mask"]
    template_reg.inputs.reg_script = template_reg_script

    #setting SelectFiles for the commonspace registration
    anat_to_template_inverse_warp = output_folder+'/'+opj('commonspace_datasink','ants_dbm_outputs','ants_dbm','output','secondlevel','secondlevel_sub-{subject_id}*_ses-{session}*_preproc*1InverseWarp.nii.gz')
    anat_to_template_warp = output_folder+'/'+opj('commonspace_datasink','ants_dbm_outputs','ants_dbm','output','secondlevel','secondlevel_sub-{subject_id}*_ses-{session}*_preproc*1Warp.nii.gz')
    anat_to_template_affine = output_folder+'/'+opj('commonspace_datasink','ants_dbm_outputs','ants_dbm','output','secondlevel','secondlevel_sub-{subject_id}*_ses-{session}*_preproc*0GenericAffine.mat')
    warped_anat = output_folder+'/'+opj('commonspace_datasink','ants_dbm_outputs','ants_dbm','output','secondlevel','secondlevel_template0sub-{subject_id}*_ses-{session}*_preproc*WarpedToTemplate.nii.gz')
    template_to_common_affine = '/'+opj('{template_to_common_affine}')
    template_to_common_warp = '/'+opj('{template_to_common_warp}')
    template_to_common_inverse_warp = '/'+opj('{template_to_common_inverse_warp}')

    commonspace_templates = {'anat_to_template_inverse_warp':anat_to_template_inverse_warp,'anat_to_template_warp': anat_to_template_warp, 'anat_to_template_affine': anat_to_template_affine, 'template_to_common_affine':template_to_common_affine, 'template_to_common_warp': template_to_common_warp, 'template_to_common_inverse_warp':template_to_common_inverse_warp, 'warped_anat':warped_anat}

    commonspace_selectfiles = pe.Node(SelectFiles(commonspace_templates),
                   name="commonspace_selectfiles")

    def transform_masks(reference_image,anat_to_template_inverse_warp, anat_to_template_affine,template_to_common_affine,template_to_common_inverse_warp):
        import os
        from rabies.preprocess_bold_pkg.utils import run_command
        cwd = os.getcwd()
        subject_id=os.path.basename(reference_image).split('_ses-')[0]
        session=os.path.basename(reference_image).split('_ses-')[1][0]
        filename_template = '%s_ses-%s' % (subject_id, session)

        input_image=os.environ["template_mask"]
        brain_mask='%s/%s_%s' % (cwd, filename_template, 'anat_mask.nii.gz')
        command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (input_image,anat_to_template_inverse_warp, anat_to_template_affine,template_to_common_inverse_warp,template_to_common_affine,reference_image,brain_mask,)
        rc = run_command(command)
        if not os.path.isfile(brain_mask):
            raise ValueError("Missing output mask. Transform call failed: "+antsApplyTransforms_call)

        input_image=os.environ["WM_mask"]
        WM_mask='%s/%s_%s' % (cwd, filename_template, 'WM_mask.nii.gz')
        command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (input_image,anat_to_template_inverse_warp, anat_to_template_affine,template_to_common_inverse_warp,template_to_common_affine,reference_image,WM_mask,)
        rc = run_command(command)
        if not os.path.isfile(WM_mask):
            raise ValueError("Missing output mask. Transform call failed: "+antsApplyTransforms_call)

        input_image=os.environ["CSF_mask"]
        CSF_mask='%s/%s_%s' % (cwd, filename_template, 'CSF_mask.nii.gz')
        command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (input_image,anat_to_template_inverse_warp, anat_to_template_affine,template_to_common_inverse_warp,template_to_common_affine,reference_image,CSF_mask,)
        rc = run_command(command)
        if not os.path.isfile(CSF_mask):
            raise ValueError("Missing output mask. Transform call failed: "+antsApplyTransforms_call)

        input_image=os.environ["vascular_mask"]
        vascular_mask='%s/%s_%s' % (cwd, filename_template, 'vascular_mask.nii.gz')
        command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (input_image,anat_to_template_inverse_warp, anat_to_template_affine,template_to_common_inverse_warp,template_to_common_affine,reference_image,vascular_mask,)
        rc = run_command(command)
        if not os.path.isfile(vascular_mask):
            raise ValueError("Missing output mask. Transform call failed: "+antsApplyTransforms_call)

        input_image=os.environ["atlas_labels"]
        anat_labels='%s/%s_%s' % (cwd, filename_template, 'atlas_labels.nii.gz')
        command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (input_image,anat_to_template_inverse_warp, anat_to_template_affine,template_to_common_inverse_warp,template_to_common_affine,reference_image,anat_labels,)
        rc = run_command(command)
        if not os.path.isfile(anat_labels):
            raise ValueError("Missing output mask. Transform call failed: "+antsApplyTransforms_call)

        return brain_mask, WM_mask, CSF_mask, vascular_mask, anat_labels

    transform_masks = pe.Node(Function(input_names=['reference_image','anat_to_template_inverse_warp', 'anat_to_template_affine','template_to_common_affine','template_to_common_inverse_warp'],
                              output_names=['brain_mask', 'WM_mask', 'CSF_mask', 'vascular_mask', 'anat_labels'],
                              function=transform_masks),
                     name='transform_masks')

    def commonspace_transforms(template_to_common_warp,template_to_common_affine,anat_to_template_warp, anat_to_template_affine):
        return [template_to_common_warp,template_to_common_affine,anat_to_template_warp, anat_to_template_affine],[0,0,0,0] #transforms_list,inverses
    commonspace_transforms_prep = pe.Node(Function(input_names=['template_to_common_warp','template_to_common_affine','anat_to_template_warp','anat_to_template_affine'],
                              output_names=['transforms_list','inverses'],
                              function=commonspace_transforms),
                     name='commonspace_transforms_prep')

    bold_main_wf=init_bold_main_wf(apply_despiking=apply_despiking, tr=tr, tpattern=tpattern, apply_STC=apply_STC, detect_dummy=detect_dummy, slice_mc=slice_mc, data_dir_path=data_dir_path, bias_reg_script=bias_reg_script, coreg_script=coreg_script, nativespace_resampling=nativespace_resampling, commonspace_resampling=commonspace_resampling)

    workflow.connect([
        (anat_preproc_wf, joinnode_session, [("outputnode.preproc_anat", "file_list")]),
        (joinnode_sub_id, commonspace_reg, [
            ("file_list", "file_list"),
            ]),
        (infosub_id, commonspace_selectfiles, [
            ("subject_id", "subject_id"),
            ]),
        (infosession, commonspace_selectfiles, [
            ("session", "session")
            ]),
        (commonspace_reg, template_reg, [
            ("ants_dbm_template", "moving_image"),
            ]),
        (commonspace_reg, commonspace_selectfiles, [
            ("ants_dbm_template", "ants_dbm_template"),
            ]),
        (template_reg, commonspace_selectfiles, [
            ("affine", "template_to_common_affine"),
            ("warp", "template_to_common_warp"),
            ("inverse_warp", "template_to_common_inverse_warp"),
            ]),
        (commonspace_selectfiles, transform_masks, [
            ("template_to_common_affine","template_to_common_affine"),
            ("template_to_common_inverse_warp","template_to_common_inverse_warp"),
            ("anat_to_template_affine", "anat_to_template_affine"),
            ("anat_to_template_inverse_warp", "anat_to_template_inverse_warp"),
            ]),
        (anat_preproc_wf, transform_masks, [
            ("outputnode.preproc_anat", "reference_image"),
            ]),
        (anat_preproc_wf, bold_main_wf, [
            ("outputnode.preproc_anat", "inputnode.anat_preproc"),
            ]),
        (transform_masks, bold_main_wf, [
            ("anat_labels", 'inputnode.labels'),
            ("brain_mask", 'inputnode.anat_mask'),
            ("WM_mask", "inputnode.WM_mask"),
            ("CSF_mask", "inputnode.CSF_mask"),
            ("vascular_mask", "inputnode.vascular_mask"),
            ]),
        (transform_masks, outputnode, [
            ("anat_labels", 'anat_labels'),
            ("brain_mask", 'anat_mask'),
            ("WM_mask", "WM_mask"),
            ("CSF_mask", "CSF_mask"),
            ]),
        (commonspace_selectfiles, bold_main_wf, [
            ("template_to_common_affine","inputnode.template_to_common_affine"),
            ("template_to_common_warp","inputnode.template_to_common_warp"),
            ("anat_to_template_affine", "inputnode.anat_to_template_affine"),
            ("anat_to_template_warp", "inputnode.anat_to_template_warp"),
            ]),
        (bold_main_wf, outputnode, [
            ("outputnode.commonspace_bold", "commonspace_bold"),
            ("outputnode.commonspace_mask", "commonspace_mask"),
            ("outputnode.commonspace_WM_mask", "commonspace_WM_mask"),
            ("outputnode.commonspace_CSF_mask", "commonspace_CSF_mask"),
            ("outputnode.commonspace_labels", "commonspace_labels"),
            ]),
        (commonspace_reg, commonspace_datasink, [
            ("ants_dbm_template", "ants_dbm_template"),
            ]),
        (template_reg, commonspace_datasink, [
            ("warped_image", "warped_template"),
            ]),
        (template_reg, transforms_datasink, [
            ("affine", "template_to_common_affine"),
            ("warp", "template_to_common_warp"),
            ("inverse_warp", "template_to_common_inverse_warp"),
            ]),
        (commonspace_selectfiles, transforms_datasink, [
            ("anat_to_template_affine", "anat_to_template_affine"),
            ("anat_to_template_warp", "anat_to_template_warp"),
            ("anat_to_template_inverse_warp", "anat_to_template_inverse_warp"),
            ]),
        (outputnode, anat_datasink, [
            ("anat_labels", 'anat_labels'),
            ("anat_mask", 'anat_mask'),
            ("WM_mask", "WM_mask"),
            ("CSF_mask", "CSF_mask"),
            ]),
        (outputnode, bold_datasink, [
            ("commonspace_bold", "commonspace_bold"), #resampled EPI after motion realignment and SDC
            ("commonspace_mask", "commonspace_bold_mask"),
            ("commonspace_WM_mask", "commonspace_bold_WM_mask"),
            ("commonspace_CSF_mask", "commonspace_bold_CSF_mask"),
            ("commonspace_labels", "commonspace_bold_labels"),
            ]),
        ])

    # MAIN WORKFLOW STRUCTURE #######################################################
    workflow.connect([
        (anat_selectfiles, anat_convert_to_RAS_node, [("out_file", "img_file")]),
        (anat_convert_to_RAS_node, anat_preproc_wf, [("RAS_file", "inputnode.anat_file")]),
        (resample_template_node, anat_preproc_wf, [("resampled_template", "inputnode.template_anat")]),
        (resample_template_node, commonspace_reg, [("resampled_template", "template_anat")]),
        (resample_template_node, template_reg, [("resampled_template", "fixed_image")]),
        (anat_preproc_wf, anat_datasink, [("outputnode.preproc_anat", "anat_preproc")]),
        (bold_selectfiles, bold_datasink, [
            ("out_file", "input_bold"),
            ]),
        (bold_selectfiles, bold_convert_to_RAS_node, [('out_file', 'img_file')]),
        (bold_convert_to_RAS_node, bold_main_wf, [
            ("RAS_file", "inputnode.bold"),
            ]),
        (resample_template_node, bold_main_wf, [("resampled_template", "inputnode.template_anat")]),
        (bold_main_wf, outputnode, [
            ("outputnode.bold_ref", "initial_bold_ref"),
            ("outputnode.corrected_EPI", "bias_cor_bold"),
            ("outputnode.EPI_brain_mask", "bold_brain_mask"),
            ("outputnode.EPI_WM_mask", "bold_WM_mask"),
            ("outputnode.EPI_CSF_mask", "bold_CSF_mask"),
            ("outputnode.EPI_labels", "bold_labels"),
            ("outputnode.confounds_csv", "confounds_csv"),
            ("outputnode.FD_voxelwise", "FD_voxelwise"),
            ("outputnode.pos_voxelwise", "pos_voxelwise"),
            ("outputnode.FD_csv", "FD_csv"),
            ('outputnode.affine_bold2anat', 'affine_bold2anat'),
            ('outputnode.warp_bold2anat', 'warp_bold2anat'),
            ('outputnode.inverse_warp_bold2anat', 'inverse_warp_bold2anat'),
            ("outputnode.output_warped_bold", "bias_cor_bold_warped2anat"),
            ("outputnode.resampled_bold", "native_corrected_bold"),
            ("outputnode.resampled_ref_bold", "corrected_bold_ref"),
            ]),
        (outputnode, transforms_datasink, [
            ('affine_bold2anat', 'affine_bold2anat'),
            ('warp_bold2anat', 'warp_bold2anat'),
            ('inverse_warp_bold2anat', 'inverse_warp_bold2anat'),
            ]),
        (outputnode, confounds_datasink, [
            ("confounds_csv", "confounds_csv"), #confounds file
            ("FD_voxelwise", "FD_voxelwise"),
            ("pos_voxelwise", "pos_voxelwise"),
            ("FD_csv", "FD_csv"),
            ]),
        (outputnode, bold_datasink, [
            ("initial_bold_ref","initial_bold_ref"), #inspect initial bold ref
            ("bias_cor_bold","bias_cor_bold"), #inspect bias correction
            ("bold_brain_mask","bold_brain_mask"), #get the EPI labels
            ("bold_WM_mask","bold_WM_mask"), #get the EPI labels
            ("bold_CSF_mask","bold_CSF_mask"), #get the EPI labels
            ("bold_labels","bold_labels"), #get the EPI labels
            ("bias_cor_bold_warped2anat","bias_cor_bold_warped2anat"), #warped EPI to anat
            ("native_corrected_bold", "corrected_bold"), #resampled EPI after motion realignment and SDC
            ("corrected_bold_ref", "corrected_bold_ref"), #resampled EPI after motion realignment and SDC
            ]),
        ])

    #organizing .png outputs for QC
    PlotMotionTrace_node = pe.Node(PlotMotionTrace(), name='PlotMotionTrace')
    PlotMotionTrace_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_EPI2Anat_node = pe.Node(PlotOverlap(), name='PlotOverlap_EPI2Anat')
    PlotOverlap_EPI2Anat_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_EPI2Anat_node.inputs.reg_name = 'EPI2Anat'
    PlotOverlap_Anat2Template_node = pe.Node(PlotOverlap(), name='PlotOverlap_Anat2Template')
    PlotOverlap_Anat2Template_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_Anat2Template_node.inputs.reg_name = 'Anat2Template'
    PlotOverlap_Template2Commonspace_node = pe.Node(PlotOverlap(), name='PlotOverlap_Template2Commonspace')
    PlotOverlap_Template2Commonspace_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_Template2Commonspace_node.inputs.reg_name = 'Template2Commonspace'

    workflow.connect([
        (anat_preproc_wf, PlotOverlap_EPI2Anat_node, [("outputnode.preproc_anat", "fixed")]),
        (resample_template_node, PlotOverlap_Template2Commonspace_node, [("resampled_template", "fixed")]),
        (outputnode, PlotOverlap_EPI2Anat_node, [
            ("bias_cor_bold_warped2anat","moving"), #warped EPI to anat
            ]),
        (outputnode, PlotMotionTrace_node, [
            ("confounds_csv", "confounds_csv"), #confounds file
            ]),
        (commonspace_selectfiles, PlotOverlap_Anat2Template_node, [
            ("warped_anat", "moving"),
            ]),
        (commonspace_reg, PlotOverlap_Anat2Template_node, [
            ("ants_dbm_template", "fixed"),
            ]),
        (template_reg, PlotOverlap_Template2Commonspace_node, [
            ("warped_image", "moving"),
            ]),
        ])

    ###Confound regression step
    if CR_meta['apply_CR']:
        confound_regression_wf=init_confound_regression_wf(lowpass=CR_meta['lowpass'], highpass=CR_meta['highpass'],
            smoothing_filter=CR_meta['smoothing_filter'], run_aroma=CR_meta['run_aroma'], aroma_dim=CR_meta['aroma_dim'], conf_list=CR_meta['conf_list'], TR=CR_meta['TR'], apply_scrubbing=CR_meta['apply_scrubbing'],
            scrubbing_threshold=CR_meta['scrubbing_threshold'], timeseries_interval=CR_meta['timeseries_interval'], diagnosis_output=CR_meta['diagnosis_output'], seed_list=CR_meta['seed_list'])
        workflow.connect([
            (outputnode, confound_regression_wf, [
                ("confounds_csv", "inputnode.confounds_file"), #confounds file
                ("FD_csv", "inputnode.FD_file"),
                ]),
            ])
        if CR_meta['commonspace_bold']:
            workflow.connect([
                (outputnode, confound_regression_wf, [
                    ("commonspace_bold", "inputnode.bold_file"),
                    ("commonspace_mask","inputnode.brain_mask"),
                    ("commonspace_CSF_mask","inputnode.csf_mask"),
                    ]),
                ])
        else:
            workflow.connect([
                (outputnode, confound_regression_wf, [
                    ("native_corrected_bold", "inputnode.bold_file"),
                    ("bold_brain_mask","inputnode.brain_mask"),
                    ("bold_CSF_mask","inputnode.csf_mask"),
                    ]),
                ])

        confound_regression_datasink = pe.Node(DataSink(base_directory=output_folder,
                                 container="confound_regression_datasink"),
                        name="confound_regression_datasink")

        workflow.connect([
            (confound_regression_wf, confound_regression_datasink, [
                ("outputnode.cleaned_path", "cleaned_timeseries"),
                ("outputnode.aroma_out","aroma_outputs"),
                ("outputnode.mel_out","subject_melodic_ICA"),
                ("outputnode.tSNR_file","tSNR_map"),
                ("outputnode.corr_map_list","seed_correlation_maps"),
                ]),
            ])

    return workflow
