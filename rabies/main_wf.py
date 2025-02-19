import os
from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu
from .preprocess_pkg.anat_preproc import init_anat_preproc_wf
from .preprocess_pkg.commonspace import ANTsDBM
from .preprocess_pkg.bold_main_wf import init_bold_main_wf
from .preprocess_pkg.registration import run_antsRegistration
from .preprocess_pkg.utils import BIDSDataGraber, prep_bids_iter, convert_to_RAS
from .QC_report import PlotOverlap, PlotMotionTrace
from nipype.interfaces.io import DataSink

from nipype.interfaces.utility import Function


def init_main_wf(data_dir_path, output_folder, opts, cr_opts=None, analysis_opts=None, name='main_wf'):
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
        no_STC
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

    # set output node
    outputnode = pe.Node(niu.IdentityInterface(
        fields=['anat_preproc', 'anat_mask', 'anat_labels', 'WM_mask', 'CSF_mask', 'initial_bold_ref', 'bias_cor_bold', 'affine_bold2anat', 'warp_bold2anat', 'inverse_warp_bold2anat', 'bias_cor_bold_warped2anat', 'native_corrected_bold', 'corrected_bold_ref', 'confounds_csv', 'FD_voxelwise', 'pos_voxelwise', 'FD_csv',
                'bold_brain_mask', 'bold_WM_mask', 'bold_CSF_mask', 'bold_labels', 'commonspace_bold', 'commonspace_mask', 'commonspace_WM_mask', 'commonspace_CSF_mask', 'commonspace_vascular_mask', 'commonspace_labels']),
        name='outputnode')

    from bids.layout import BIDSLayout
    layout = BIDSLayout(data_dir_path, validate=False)
    split_name, scan_info, run_iter, scan_list = prep_bids_iter(
        layout, opts.bold_only)

    # setting up all iterables
    main_split = pe.Node(niu.IdentityInterface(fields=['split_name', 'scan_info']),
                         name="main_split")
    main_split.iterables = [('split_name', split_name),
                            ('scan_info', scan_info)]
    main_split.synchronize = True

    bold_selectfiles = pe.Node(BIDSDataGraber(bids_dir=data_dir_path, suffix=[
                               'bold', 'cbv']), name='bold_selectfiles')

    # node to conver input image to consistent RAS orientation
    bold_convert_to_RAS_node = pe.Node(Function(input_names=['img_file'],
                                                output_names=['RAS_file'],
                                                function=convert_to_RAS),
                                       name='bold_convert_to_RAS')

    # Resample the anatomical template according to the resolution of the provided input data
    from rabies.preprocess_pkg.utils import resample_template
    resample_template_node = pe.Node(Function(input_names=['template_file', 'file_list', 'spacing', 'rabies_data_type'],
                                              output_names=[
                                                  'resampled_template'],
                                              function=resample_template),
                                     name='resample_template', mem_gb=1*opts.scale_min_memory)
    resample_template_node.inputs.template_file = str(opts.anat_template)
    resample_template_node.inputs.spacing = opts.anatomical_resampling
    resample_template_node.inputs.file_list = scan_list
    resample_template_node.inputs.rabies_data_type = opts.data_type

    # calculate the number of scans that will be registered
    num_scan = len(scan_list)
    if opts.local_threads < num_scan:
        num_scan = opts.local_threads

    # execute the registration of the generate anatomical template with the provided atlas for labeling and masking
    template_reg = pe.Node(Function(input_names=['reg_script', 'moving_image', 'fixed_image', 'anat_mask', 'rabies_data_type'],
                                    output_names=['affine', 'warp',
                                                  'inverse_warp', 'warped_image'],
                                    function=run_antsRegistration),
                           name='template_reg', mem_gb=2*opts.scale_min_memory)
    template_reg.inputs.anat_mask = str(opts.brain_mask)
    template_reg.inputs.rabies_data_type = opts.data_type

    bold_main_wf = init_bold_main_wf(opts=opts)

    if opts.fast_commonspace:
        import rabies
        dir_path = os.path.dirname(os.path.realpath(rabies.__file__))
        reg_script = dir_path+'/shell_scripts/null_nonlin.sh'
        template_reg.inputs.reg_script = str(reg_script)

        commonspace_reg = pe.Node(Function(input_names=['reg_script', 'moving_image', 'fixed_image', 'anat_mask', 'rabies_data_type'],
                                           output_names=['affine', 'warp',
                                                         'inverse_warp', 'warped_image'],
                                           function=run_antsRegistration),
                                  name='commonspace_reg', mem_gb=2*opts.scale_min_memory)
        commonspace_reg.plugin_args = {
            'qsub_args': '-pe smp %s' % (str(3*opts.min_proc)), 'overwrite': True}
        commonspace_reg.inputs.anat_mask = str(opts.brain_mask)
        commonspace_reg.inputs.reg_script = str(opts.template_reg_script)
        commonspace_reg.inputs.rabies_data_type = opts.data_type

        commonspace_selectfiles = pe.Node(niu.IdentityInterface(fields=['anat_to_template_affine', 'anat_to_template_warp', 'anat_to_template_inverse_warp', 'warped_anat']),
                                          name="commonspace_selectfiles")

        workflow.connect([
            (resample_template_node, commonspace_reg, [
             ("resampled_template", "fixed_image")]),
            (commonspace_reg, template_reg, [
                ("warped_image", "moving_image"),
                ]),
            (commonspace_reg, commonspace_selectfiles, [
                ("affine", "anat_to_template_affine"),
                ("warp", "anat_to_template_warp"),
                ("inverse_warp", "anat_to_template_inverse_warp"),
                ("warped_image", "warped_anat"),
                ]),
            ])
    else:
        template_reg.plugin_args = {
            'qsub_args': '-pe smp %s' % (str(3*opts.min_proc)), 'overwrite': True}
        template_reg.inputs.reg_script = str(opts.template_reg_script)

        # setting up commonspace registration within the workflow
        commonspace_mem = 1*num_scan*opts.scale_min_memory
        commonspace_reg = pe.JoinNode(ANTsDBM(output_folder=output_folder+'/commonspace_datasink/', cluster_type=opts.cluster_type,
                                              walltime=opts.walltime, memory_request=str(int(commonspace_mem))+'gb', local_threads=opts.local_threads),
                                      joinsource='main_split', joinfield=['moving_image'],
                                      name='commonspace_reg', n_procs=num_scan, mem_gb=commonspace_mem)

        # setup a node to select the proper files associated with a given input scan for commonspace registration
        commonspace_selectfiles = pe.Node(Function(input_names=['filename', 'affine_list', 'warp_list', 'inverse_warp_list', 'warped_anat_list'],
                                                   output_names=[
                                                       'anat_to_template_affine', 'anat_to_template_warp', 'anat_to_template_inverse_warp', 'warped_anat'],
                                                   function=select_commonspace_outputs),
                                          name='commonspace_selectfiles')

        workflow.connect([
            (resample_template_node, commonspace_reg, [
             ("resampled_template", "template_anat")]),
            (commonspace_reg, template_reg, [
                ("warped_image", "moving_image"),
                ]),
            (commonspace_reg, commonspace_selectfiles, [
                ("affine_list", "affine_list"),
                ("warp_list", "warp_list"),
                ("inverse_warp_list", "inverse_warp_list"),
                ("warped_anat_list", "warped_anat_list"),
                ]),
            ])

    # MAIN WORKFLOW STRUCTURE #######################################################
    workflow.connect([
        (main_split, bold_selectfiles, [
            ("scan_info", "scan_info"),
            ]),
        (main_split, commonspace_selectfiles, [
            ("split_name", "filename"),
            ]),
        (bold_selectfiles, bold_convert_to_RAS_node, [
            ('out_file', 'img_file'),
            ]),
        (bold_convert_to_RAS_node, bold_main_wf, [
            ("RAS_file", "inputnode.bold"),
            ]),
        (resample_template_node, template_reg, [
         ("resampled_template", "fixed_image")]),
        (resample_template_node, bold_main_wf, [
         ("resampled_template", "inputnode.template_anat")]),
        (template_reg, bold_main_wf, [
            ("affine", "inputnode.template_to_common_affine"),
            ("warp", "inputnode.template_to_common_warp"),
            ]),
        (commonspace_selectfiles, bold_main_wf, [
            ("anat_to_template_affine", "inputnode.anat_to_template_affine"),
            ("anat_to_template_warp", "inputnode.anat_to_template_warp"),
            ]),
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
            ("outputnode.commonspace_bold", "commonspace_bold"),
            ("outputnode.commonspace_mask", "commonspace_mask"),
            ("outputnode.commonspace_WM_mask", "commonspace_WM_mask"),
            ("outputnode.commonspace_CSF_mask", "commonspace_CSF_mask"),
            ("outputnode.commonspace_vascular_mask", "commonspace_vascular_mask"),
            ("outputnode.commonspace_labels", "commonspace_labels"),
            ]),
        ])

    if not opts.bold_only:
        run_split = pe.Node(niu.IdentityInterface(fields=['run', 'split_name']),
                            name="run_split")
        run_split.itersource = ('main_split', 'split_name')
        run_split.iterables = [('run', run_iter)]

        anat_selectfiles = pe.Node(BIDSDataGraber(bids_dir=data_dir_path, suffix=[
                                   'T2w', 'T1w']), name='anat_selectfiles')
        anat_selectfiles.inputs.run = None

        anat_convert_to_RAS_node = pe.Node(Function(input_names=['img_file'],
                                                    output_names=['RAS_file'],
                                                    function=convert_to_RAS),
                                           name='anat_convert_to_RAS')

        # setting anat preprocessing nodes
        anat_preproc_wf = init_anat_preproc_wf(reg_script=opts.anat_reg_script,
                                               disable_anat_preproc=opts.disable_anat_preproc, rabies_data_type=opts.data_type, rabies_mem_scale=opts.scale_min_memory)
        anat_preproc_wf.inputs.inputnode.template_mask = str(opts.brain_mask)

        transform_masks = pe.Node(Function(input_names=['brain_mask_in', 'WM_mask_in', 'CSF_mask_in', 'vascular_mask_in', 'atlas_labels_in', 'reference_image', 'anat_to_template_inverse_warp', 'anat_to_template_affine', 'template_to_common_affine', 'template_to_common_inverse_warp'],
                                           output_names=[
                                               'brain_mask', 'WM_mask', 'CSF_mask', 'vascular_mask', 'anat_labels'],
                                           function=transform_masks_anat),
                                  name='transform_masks')
        transform_masks.inputs.brain_mask_in = str(opts.brain_mask)
        transform_masks.inputs.WM_mask_in = str(opts.WM_mask)
        transform_masks.inputs.CSF_mask_in = str(opts.CSF_mask)
        transform_masks.inputs.vascular_mask_in = str(opts.vascular_mask)
        transform_masks.inputs.atlas_labels_in = str(opts.labels)

        workflow.connect([
            (main_split, run_split, [
                ("split_name", "split_name"),
                ]),
            (main_split, anat_selectfiles,
             [("scan_info", "scan_info")]),
            (run_split, bold_selectfiles, [
                ("run", "run"),
                ]),
            (anat_selectfiles, anat_convert_to_RAS_node,
             [("out_file", "img_file")]),
            (anat_convert_to_RAS_node, anat_preproc_wf,
             [("RAS_file", "inputnode.anat_file")]),
            (resample_template_node, anat_preproc_wf, [
             ("resampled_template", "inputnode.template_anat")]),
            (anat_preproc_wf, commonspace_reg, [
             ("outputnode.preproc_anat", "moving_image")]),
            (anat_preproc_wf, transform_masks, [
                ("outputnode.preproc_anat", "reference_image"),
                ]),
            (anat_preproc_wf, bold_main_wf, [
                ("outputnode.preproc_anat", "inputnode.anat_preproc"),
                ]),
            (template_reg, transform_masks, [
                ("affine", "template_to_common_affine"),
                ("inverse_warp", "template_to_common_inverse_warp"),
                ]),
            (commonspace_selectfiles, transform_masks, [
                ("anat_to_template_affine", "anat_to_template_affine"),
                ("anat_to_template_inverse_warp", "anat_to_template_inverse_warp"),
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
            ])

    else:
        bold_main_wf.inputs.inputnode.anat_mask = str(opts.brain_mask)
        bold_main_wf.inputs.inputnode.WM_mask = str(opts.WM_mask)
        bold_main_wf.inputs.inputnode.CSF_mask = str(opts.CSF_mask)
        bold_main_wf.inputs.inputnode.vascular_mask = str(opts.vascular_mask)
        bold_main_wf.inputs.inputnode.labels = str(opts.atlas_labels)

        bias_cor_bold_main_wf = init_bold_main_wf(
            bias_cor_only=True, name='bias_cor_bold_main_wf', opts=opts)
        bias_cor_bold_main_wf.inputs.inputnode.anat_mask = str(opts.brain_mask)

        workflow.connect([
            (bold_convert_to_RAS_node, bias_cor_bold_main_wf, [
                ("RAS_file", "inputnode.bold"),
                ]),
            (resample_template_node, bias_cor_bold_main_wf, [
             ("resampled_template", "inputnode.template_anat")]),
            (bias_cor_bold_main_wf, bold_main_wf, [
                ("transitionnode.bold_file", "transitionnode.bold_file"),
                ("transitionnode.bold_ref", "transitionnode.bold_ref"),
                ("transitionnode.corrected_EPI", "transitionnode.corrected_EPI"),
                ]),
            (bias_cor_bold_main_wf, commonspace_reg, [
             ("transitionnode.corrected_EPI", "moving_image")]),
            ])

    # organizing .png outputs for QC
    PlotMotionTrace_node = pe.Node(PlotMotionTrace(), name='PlotMotionTrace')
    PlotMotionTrace_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_Anat2Template_node = pe.Node(
        PlotOverlap(), name='PlotOverlap_Anat2Template')
    PlotOverlap_Anat2Template_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_Anat2Template_node.inputs.reg_name = 'Anat2Template'
    PlotOverlap_Template2Commonspace_node = pe.Node(
        PlotOverlap(), name='PlotOverlap_Template2Commonspace')
    PlotOverlap_Template2Commonspace_node.inputs.out_dir = output_folder+'/QC_report'
    PlotOverlap_Template2Commonspace_node.inputs.reg_name = 'Template2Commonspace'
    PlotOverlap_Template2Commonspace_node.inputs.split_name = ''
    PlotOverlap_Template2Commonspace_node.inputs.name_source = ''

    if not opts.bold_only:
        PlotOverlap_EPI2Anat_node = pe.Node(
            PlotOverlap(), name='PlotOverlap_EPI2Anat')
        PlotOverlap_EPI2Anat_node.inputs.out_dir = output_folder+'/QC_report'
        PlotOverlap_EPI2Anat_node.inputs.reg_name = 'EPI2Anat'
        workflow.connect([
            (main_split, PlotOverlap_EPI2Anat_node,
             [("split_name", "split_name")]),
            (bold_selectfiles, PlotOverlap_EPI2Anat_node,
             [("out_file", "name_source")]),
            (anat_preproc_wf, PlotOverlap_EPI2Anat_node,
             [("outputnode.preproc_anat", "fixed")]),
            (outputnode, PlotOverlap_EPI2Anat_node, [
                ("bias_cor_bold_warped2anat", "moving"),  # warped EPI to anat
                ]),
            (anat_selectfiles, PlotOverlap_Anat2Template_node, [
                ("out_file", "name_source"),
                ]),
            ])
    else:
        workflow.connect([
            (bold_selectfiles, PlotOverlap_Anat2Template_node, [
                ("out_file", "name_source"),
                ]),
            ])

    workflow.connect([
        (bold_selectfiles, PlotMotionTrace_node,
         [("out_file", "name_source")]),
        (main_split, PlotMotionTrace_node,
         [("split_name", "split_name")]),
        (resample_template_node, PlotOverlap_Template2Commonspace_node,
         [("resampled_template", "fixed")]),
        (outputnode, PlotMotionTrace_node, [
            ("confounds_csv", "confounds_csv"),  # confounds file
            ]),
        (main_split, PlotOverlap_Anat2Template_node, [
            ("split_name", "split_name"),
            ]),
        (commonspace_selectfiles, PlotOverlap_Anat2Template_node, [
            ("warped_anat", "moving"),
            ]),
        (commonspace_reg, PlotOverlap_Anat2Template_node, [
            ("warped_image", "fixed"),
            ]),
        (template_reg, PlotOverlap_Template2Commonspace_node, [
            ("warped_image", "moving"),
            ]),
        ])

    # Integrate confound regression
    if cr_opts is not None:
        workflow, confound_regression_wf = integrate_confound_regression(
            workflow, outputnode, cr_opts, bold_only=opts.bold_only)

        # Integrate analysis
        if analysis_opts is not None:
            workflow = integrate_analysis(
                workflow, outputnode, confound_regression_wf, analysis_opts, opts.bold_only, cr_opts.commonspace_bold)

    elif opts.rabies_step == 'preprocess':
        # Datasink - creates output folder for important outputs
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

        workflow.connect([
            (commonspace_reg, commonspace_datasink, [
                ("warped_image", "ants_dbm_template"),
                ]),
            (bold_selectfiles, bold_datasink, [
                ("out_file", "input_bold"),
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
            (outputnode, transforms_datasink, [
                ('affine_bold2anat', 'affine_bold2anat'),
                ('warp_bold2anat', 'warp_bold2anat'),
                ('inverse_warp_bold2anat', 'inverse_warp_bold2anat'),
                ]),
            (outputnode, confounds_datasink, [
                ("confounds_csv", "confounds_csv"),  # confounds file
                ("FD_voxelwise", "FD_voxelwise"),
                ("pos_voxelwise", "pos_voxelwise"),
                ("FD_csv", "FD_csv"),
                ]),
            (outputnode, bold_datasink, [
                ("initial_bold_ref", "initial_bold_ref"),  # inspect initial bold ref
                ("bias_cor_bold", "bias_cor_bold"),  # inspect bias correction
                ("bold_brain_mask", "bold_brain_mask"),  # get the EPI labels
                ("bold_WM_mask", "bold_WM_mask"),  # get the EPI labels
                ("bold_CSF_mask", "bold_CSF_mask"),  # get the EPI labels
                ("bold_labels", "bold_labels"),  # get the EPI labels
                # warped EPI to anat
                ("bias_cor_bold_warped2anat", "bias_cor_bold_warped2anat"),
                # resampled EPI after motion realignment and SDC
                ("native_corrected_bold", "corrected_bold"),
                # resampled EPI after motion realignment and SDC
                ("corrected_bold_ref", "corrected_bold_ref"),
                # resampled EPI after motion realignment and SDC
                ("commonspace_bold", "commonspace_bold"),
                ("commonspace_mask", "commonspace_bold_mask"),
                ("commonspace_WM_mask", "commonspace_bold_WM_mask"),
                ("commonspace_CSF_mask", "commonspace_bold_CSF_mask"),
                ("commonspace_vascular_mask", "commonspace_vascular_mask"),
                ("commonspace_labels", "commonspace_bold_labels"),
                ]),
            ])

        if not opts.bold_only:
            anat_datasink = pe.Node(DataSink(base_directory=output_folder,
                                             container="anat_datasink"),
                                    name="anat_datasink")

            workflow.connect([
                (anat_preproc_wf, anat_datasink, [
                 ("outputnode.preproc_anat", "anat_preproc")]),
                (outputnode, anat_datasink, [
                    ("anat_labels", 'anat_labels'),
                    ("anat_mask", 'anat_mask'),
                    ("WM_mask", "WM_mask"),
                    ("CSF_mask", "CSF_mask"),
                    ]),
                ])

    return workflow


def integrate_confound_regression(workflow, outputnode, cr_opts, bold_only):
    cr_output = os.path.abspath(str(cr_opts.output_dir))

    from rabies.conf_reg_pkg.confound_regression import init_confound_regression_wf
    confound_regression_wf = init_confound_regression_wf(lowpass=cr_opts.lowpass, highpass=cr_opts.highpass,
                                                         smoothing_filter=cr_opts.smoothing_filter, run_aroma=cr_opts.run_aroma, aroma_dim=cr_opts.aroma_dim, conf_list=cr_opts.conf_list, TR=cr_opts.TR, apply_scrubbing=cr_opts.apply_scrubbing,
                                                         scrubbing_threshold=cr_opts.scrubbing_threshold, timeseries_interval=cr_opts.timeseries_interval, diagnosis_output=cr_opts.diagnosis_output, name=cr_opts.wf_name)

    workflow.connect([
        (outputnode, confound_regression_wf, [
            ("confounds_csv", "inputnode.confounds_file"),  # confounds file
            ("FD_csv", "inputnode.FD_file"),
            ]),
        ])

    if bold_only and not cr_opts.commonspace_bold:
        raise ValueError(
            'Must select --commonspace option for running confound regression on outputs from --bold_only.')

    if cr_opts.commonspace_bold:
        workflow.connect([
            (outputnode, confound_regression_wf, [
                ("commonspace_bold", "inputnode.bold_file"),
                ("commonspace_mask", "inputnode.brain_mask"),
                ("commonspace_CSF_mask", "inputnode.csf_mask"),
                ]),
            ])
    else:
        workflow.connect([
            (outputnode, confound_regression_wf, [
                ("native_corrected_bold", "inputnode.bold_file"),
                ("bold_brain_mask", "inputnode.brain_mask"),
                ("bold_CSF_mask", "inputnode.csf_mask"),
                ]),
            ])

    if cr_opts.rabies_step == 'confound_regression':
        confound_regression_datasink = pe.Node(DataSink(base_directory=cr_output,
                                                        container="confound_regression_datasink"),
                                               name="confound_regression_datasink")
        workflow.connect([
            (confound_regression_wf, confound_regression_datasink, [
                ("outputnode.cleaned_path", "cleaned_timeseries"),
                ("outputnode.VE_file", "VE_file"),
                ("outputnode.mel_out", "subject_melodic_ICA"),
                ("outputnode.tSNR_file", "tSNR_map"),
                ]),
            ])
        if cr_opts.run_aroma:
            workflow.connect([
                (confound_regression_wf, confound_regression_datasink, [
                    ("outputnode.aroma_out", "aroma_out"),
                    ]),
                ])

    return workflow, confound_regression_wf


def integrate_analysis(workflow, outputnode, confound_regression_wf, analysis_opts, bold_only, commonspace_bold):
    analysis_output = os.path.abspath(str(analysis_opts.output_dir))

    from rabies.analysis_pkg.analysis_wf import init_analysis_wf
    analysis_wf = init_analysis_wf(
        opts=analysis_opts, commonspace_cr=commonspace_bold, seed_list=analysis_opts.seed_list)

    analysis_datasink = pe.Node(DataSink(base_directory=analysis_output,
                                         container="analysis_datasink"),
                                name="analysis_datasink")

    analysis_joinnode_main = pe.JoinNode(niu.IdentityInterface(fields=['file_list', 'mask_file']),
                                         name='analysis_joinnode_main',
                                         joinsource='main_split',
                                         joinfield=['file_list'])

    if commonspace_bold or bold_only:
        workflow.connect([
            (outputnode, analysis_wf, [
                ("commonspace_mask", "subject_inputnode.mask_file"),
                ("commonspace_labels", "subject_inputnode.atlas_file"),
                ]),
            ])
    else:
        workflow.connect([
            (outputnode, analysis_wf, [
                ("bold_brain_mask", "subject_inputnode.mask_file"),
                ("bold_labels", "subject_inputnode.atlas_file"),
                ]),
            ])

    workflow.connect([
        (confound_regression_wf, analysis_wf, [
            ("outputnode.cleaned_path", "subject_inputnode.bold_file"),
            ]),
        (analysis_joinnode_main, analysis_wf, [
            ("file_list", "group_inputnode.bold_file_list"),
            ("mask_file", "group_inputnode.commonspace_mask"),
            ]),
        (analysis_wf, analysis_datasink, [
            ("outputnode.group_ICA_dir", "group_ICA_dir"),
            ("outputnode.IC_file", "group_IC_file"),
            ("outputnode.DR_data_file", "DR_data_file"),
            ("outputnode.DR_nii_file", "DR_nii_file"),
            ("outputnode.matrix_data_file", "matrix_data_file"),
            ("outputnode.matrix_fig", "matrix_fig"),
            ("outputnode.corr_map_file", "seed_correlation_maps"),
            ]),
        ])
    if bold_only:
        workflow.connect([
            (outputnode, analysis_joinnode_main, [
                ("commonspace_mask", "mask_file"),
                ]),
            (confound_regression_wf, analysis_joinnode_main, [
                ("outputnode.cleaned_path", "file_list"),
                ]),
            ])
    else:
        analysis_joinnode_run = pe.JoinNode(niu.IdentityInterface(fields=['file_list', 'mask_file']),
                                            name='analysis_joinnode_run',
                                            joinsource='run_split',
                                            joinfield=['file_list'])

        workflow.connect([
            (outputnode, analysis_joinnode_run, [
                ("commonspace_mask", "mask_file"),
                ]),
            (confound_regression_wf, analysis_joinnode_run, [
                ("outputnode.cleaned_path", "file_list"),
                ]),
            (analysis_joinnode_run, analysis_joinnode_main, [
                ("file_list", "file_list"),
                ("mask_file", "mask_file"),
                ]),
            ])
    return workflow


def select_commonspace_outputs(filename, affine_list, warp_list, inverse_warp_list, warped_anat_list):
    from rabies.preprocess_pkg.utils import select_from_list
    anat_to_template_affine = select_from_list(filename, affine_list)
    anat_to_template_warp = select_from_list(filename, warp_list)
    anat_to_template_inverse_warp = select_from_list(
        filename, inverse_warp_list)
    warped_anat = select_from_list(filename, warped_anat_list)
    return anat_to_template_affine, anat_to_template_warp, anat_to_template_inverse_warp, warped_anat


def transform_masks_anat(brain_mask_in, WM_mask_in, CSF_mask_in, vascular_mask_in, atlas_labels_in, reference_image, anat_to_template_inverse_warp, anat_to_template_affine, template_to_common_affine, template_to_common_inverse_warp):
    # function to transform atlas masks to individual anatomical scans
    import os
    from rabies.preprocess_pkg.utils import run_command
    cwd = os.getcwd()

    import pathlib  # Better path manipulation
    filename_template = pathlib.Path(reference_image).name.rsplit(".nii")[0]

    input_image = brain_mask_in
    brain_mask = '%s/%s_%s' % (cwd,
                               filename_template, 'anat_mask.nii.gz')
    command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (
        input_image, anat_to_template_inverse_warp, anat_to_template_affine, template_to_common_inverse_warp, template_to_common_affine, reference_image, brain_mask,)
    rc = run_command(command)
    if not os.path.isfile(brain_mask):
        raise ValueError(
            "Missing output mask. Transform call failed: "+command)

    input_image = WM_mask_in
    WM_mask = '%s/%s_%s' % (cwd, filename_template, 'WM_mask.nii.gz')
    command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (
        input_image, anat_to_template_inverse_warp, anat_to_template_affine, template_to_common_inverse_warp, template_to_common_affine, reference_image, WM_mask,)
    rc = run_command(command)
    if not os.path.isfile(WM_mask):
        raise ValueError(
            "Missing output mask. Transform call failed: "+command)

    input_image = CSF_mask_in
    CSF_mask = '%s/%s_%s' % (cwd, filename_template, 'CSF_mask.nii.gz')
    command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (
        input_image, anat_to_template_inverse_warp, anat_to_template_affine, template_to_common_inverse_warp, template_to_common_affine, reference_image, CSF_mask,)
    rc = run_command(command)
    if not os.path.isfile(CSF_mask):
        raise ValueError(
            "Missing output mask. Transform call failed: "+command)

    input_image = vascular_mask_in
    vascular_mask = '%s/%s_%s' % (cwd,
                                  filename_template, 'vascular_mask.nii.gz')
    command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (
        input_image, anat_to_template_inverse_warp, anat_to_template_affine, template_to_common_inverse_warp, template_to_common_affine, reference_image, vascular_mask,)
    rc = run_command(command)
    if not os.path.isfile(vascular_mask):
        raise ValueError(
            "Missing output mask. Transform call failed: "+command)

    input_image = atlas_labels_in
    anat_labels = '%s/%s_%s' % (cwd,
                                filename_template, 'atlas_labels.nii.gz')
    command = 'antsApplyTransforms -d 3 -i %s -t %s -t [%s,1] -t %s -t [%s,1] -r %s -o %s --verbose -n GenericLabel' % (
        input_image, anat_to_template_inverse_warp, anat_to_template_affine, template_to_common_inverse_warp, template_to_common_affine, reference_image, anat_labels,)
    rc = run_command(command)
    if not os.path.isfile(anat_labels):
        raise ValueError(
            "Missing output mask. Transform call failed: "+command)

    return brain_mask, WM_mask, CSF_mask, vascular_mask, anat_labels
