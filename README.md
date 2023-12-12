# Leaflet

Leaflet consists of a Bayesian mixture model that identifies latent cell states (or cell types) related to underlying alternative splicing differences in junctions. 

## Note: This repostiory is being actively modified, please feel free to submit issues if you have any questions.

## Compatibility with sequencing platforms 

- Smart-Seq2 (for now*)

## Graphical Model (update)
<img src="https://github.com/daklab/Leaflet/assets/23510936/3e147ba5-7ee8-47ae-b84c-5e99e0551acf" width="500">

## Installation

Leaflet is a Python module which currently runs on Python version 3.9 or higher. Leaflet can be directly installed from github using the following command:

```pip install git+https://github.com/daklab/Leaflet.git``` 

This will automatically install any missing package dependencies.

Alternatively, Leaflet can be installed via conda (in the future). 

## Running Leaflet Scripts

### Step 1: Intron Clustering (identifying alternatively spliced events in your data)

To start using Leaflet, you will first need to perform intron clustering using the `/Leaflet/src/clustering/Leaflet_intron_clustering.py` script. This step involves identifying intron clusters based on alternative splicing events in your single-cell RNA-seq data.

#### Instructions:

Set up paths for data files and parameters. You will need to specify the path to a GTF file for your organism. Here's an example:

```bash
gtf_file="/path/to/your/organism/genes.gtf"
output_file="/path/to/output/clustered/intron_file" #additional suffix will be added with params used
sequencing_type="single_cell" # or "bulk"
```
Prepare junction files. You should have junction files generated from your single-cell data for each pseudobulk sample. For example, you can specify the path to the junction files for brain data:

```bash
junc_files="/path/to/your/junction/files"
```   

Set the singleton flag to "False" if you want to exclude singleton clusters (no alternative splicing):

```bash
singleton="False"
```

Define where you want to store the coordinates of all junctions going into your analysis:

```bash
junc_bed_file="/path/to/your/junctions.bed"
```

Set minimum junction reads, the minimum number of cells with a junction, and intron size constraints:

```bash
min_junc_reads=10
min_num_cells_wjunc=10
min_intron=50
max_intron=500000
junc_suffix="*.juncswbarcodes" # suffix of files obtained from Regtools (make sure to append barcodes to extracted junctions!)
```

Optionally, specify whether to filter low junction ratios within clusters:

```bash
filter_low_juncratios_inclust="yes" # setting this to "no" might contain very lowly used junctions (relative to other junctions in cluster but would significantly speed up this step!)
strict_filter=True # remove intron clusters with a very high number of junctions 
```

Once you have configured these parameters, run the ```Leaflet_intron_clustering.py``` script to perform intron clustering and identify Leaflet alternatively spliced event for downstream modeling.

```bash
 python /Leaflet/src/clustering/Leaflet_intron_clustering.py --gtf_file $gtf_file --junc_files $junc_files --output_file $output_file --sequencing_type $sequencing_type --junc_suffix $junc_suffix --filter_low_juncratios_inclust $filter_low_juncratios_inclust
```

Feel free to adjust these parameters according to your specific dataset and analysis requirements.

### Step 2: Prepare Sparse Inputs for Leaflet Binomial Mixture Model

For this step, you will need the output from the intron clustering process performed in Step 1. The clustered junction file generated in Step 1 is a key input for this script.

#### Script and Usage

Use the following script, `Leaflet/src/beta-binomial-lda/01_prepare_input_coo.py`, to prepare the sparse input format required for the Leaflet binomial mixture model. This step is essential for downstream modeling.

```bash
# Define the paths to the required input files
cluster_file_example="/path/to/output/clustered/intron_file_anno_50_500000_5_5_0.01_single_cell.gz"
output_file="/path/to/BBmixture_Leaflet_input"

# Run the script with the specified parameters
python Leaflet/src/beta-binomial-lda/01_prepare_input_coo.py --intron_clusters $cluster_file --output_file $output_file --has_genes "yes" --chunk_size 20000 --train_val_test "no"
```

### Step 3: Run Leaflet Binomial Mixture model to learn latent cell states and differentially spliced junctions 

For now, please use the following jupyter notebook as a reference for how this model can be run using the output obtained in Step 2. 

```
/Leaflet/notebooks/example_analysis/run_mixture_model_TM_SS2_wanno.ipynb
```
