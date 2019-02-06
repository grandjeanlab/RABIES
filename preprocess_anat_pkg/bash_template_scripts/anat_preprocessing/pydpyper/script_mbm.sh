#!bin/bash

module load anaconda/5.0.1-python3 minc-toolkit/1.9.15 minc-stuffs/0.1.21^minc-toolkit-1.9.15 qbatch/git pydpiper/2.0.10

MBM.py --verbose --pipeline-name=mbm_atlasReg \
--num-executors 150 \
--lsq6-large-rotations \
--no-nuc \
--init-model /opt/quarantine/resources/Dorr_2008_Steadman_2013_Ullmann_2013_Richards_2011_Qiu_2016_Egan_2015_40micron/100um/DSURQE.mnc \
--maget-atlas-library /opt/quarantine/resources/Dorr_2008_Steadman_2013_Ullmann_2013_Richards_2011_Qiu_2016_Egan_2015_40micron/ex-vivo/ \
--maget-no-pairwise \
--maget-mask \
--common-space-model /opt/quarantine/resources/Dorr_2008_Steadman_2013_Ullmann_2013_Richards_2011_Qiu_2016_Egan_2015_40micron/ex-vivo/DSURQE_40micron.mnc \
--run-maget \
--files ../anat_preproc/*preproc_anat.mnc
