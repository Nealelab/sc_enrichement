#!/usr/bin/env python

from __future__ import print_function
import pandas as pd
import numpy as np
import scipy.stats as st
import argparse
import subprocess
import glob
import sys
import logging
import os
import random
import string
from pybedtools import BedTool
from argparse import Namespace


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument('--main-annot', required=True, help = 'File(s) containing the gene list to calculate partition h2 or LDscores(s).  If file(s) are detected, LDscores are generated otherwise LDscore(s) are directly used.')
    parser.add_argument('--summary-stats-files', required=True,  help = 'File(s) (already processed with munge_sumstats.py) where to apply partition LDscore, files should end with .sumstats.gz. If multiple files are used, need a comma-separated list.')
    parser.add_argument('--ldscores-prefix', required=True, help = 'Prefix for main-annot file.')
    parser.add_argument('--out', required=True, help = 'Path to save the results')

    parser.add_argument('--no_baseline', action='store_true', default=False, help = 'Do not condition on baseline annotations')

    parser.add_argument('--condition-annot', help = 'File(s) containing the gene list or ldscores for conditioning. If multiple files are used, need a comma-separated list')

    parser.add_argument('--export_ldscore_path', help = 'Path where to export the LDscores generated from --main-annot-file')


    parser.add_argument('--windowsize', type=int, default=100000, help = 'size of the window around the gene')

    parser.add_argument('--snp-list-file', default="gs://singlecellldscore/list.txt", help = 'Location of the file containing the list of SNPs to use for the generation of the LD-scores')
    parser.add_argument('--gene-anno-pos-file', default="gs://singlecellldscore/GENENAME_gene_annot.txt", help = 'Location of the file containing start and end position for each gene')

    parser.add_argument('--gene-col-name', default="GENENAME", help = 'Gene column name in the file specified in --gene-anno-pos-file')

    parser.add_argument('--tkg-weights-folder', default="gs://singlecellldscore/1000G_Phase3_weights_hm3_no_MHC", help = 'Folder containing the chr-specific files with 1000 genomes weights for running LDscore regression')
    parser.add_argument('--tkg-plink-folder', default="gs://singlecellldscore/plink_files", help = 'Folder containing the chr-specific plink files from 1000 genomes to be used to create LDscores')

    parser.add_argument('--tkg-freq-folder', default="gs://singlecellldscore/1000G_Phase3_frq", help = 'Folder containing the chr-specific plink files with 1000 genomes frequencies')

    parser.add_argument('--baseline-ldscores-folder', default="gs://singlecellldscore/baselineLD_v1.1", help = 'Folder containing the baseline chr-specific LDscores to be used for conditioning')

    parser.add_argument("--verbose", help="increase output verbosity",
                    action="store_true")
    parser.add_argument('--quantiles', type=int, default=5,required=False, help='If using a continuous annotation,the number of quantiles to split it into for regression.')
    parser.add_argument('--cont-breaks',type=str,required=False,help='Specific boundary points to split your continuous annotation on, comma separated list e.g. 0.1,0.4,0.5,0.6. ATTENTION: if you use negative values add a space in the beginning e.g. <space>-0.1,-0.4,0.5,0.6')

    args = parser.parse_args()
    if not (args.main_annot or args.summary_stats_files or args.ldscores_prefix or args.out):
        parser.error("You have to specify --main-annot-file and --summary-stats-files and --ldscores-prefix and --out")

    if (len(args.main_annot.split(',')) != len(args.ldscores_prefix.split(','))):
        parser.error("--main-annot and --ldscores-prefix should be of the same length")

    if (args.cont_breaks):
        args.quantiles = None

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    return args


def random_string(length):
    """ Generate a random string """
    return ''.join(random.choice(string.ascii_letters) for m in range(length))


def type_of_file(file_input):
    '''Want to return a noun that describes file type: rsid/genelist, binary/continuous combination'''
    x = pd.read_csv(file_input,delim_whitespace=True,header=None)
    if x.shape[1] > 1:
       noun = 'continuous'
    else:   
       noun = 'binary'
    if 'rs' in x.loc[0,0]:
        noun = noun + ' rsids'
    else:
        noun = noun + ' genelist'
    return noun

def recognize_ldscore_genelist(inputs):
    """ Recognize if the input file is a set of LDscores or if it is a genelist """

    results=[]
    inputs = inputs.split(',')
    for input in inputs:
        try:
            out = filter(bool, subprocess.check_output(['gsutil','ls',os.path.join(input, "")]).split("\n"))
        except subprocess.CalledProcessError:
            print("Some LDscores or genesets file(s) you specify do not exists")
        if len(out) > 1:
            results.append(True)
        elif len(out) == 1:
            results.append(False)
    if results.count(results[0]) == len(results):
        return(results[0])
    else:
        sys.exit("Not all files are of the same type, check you didn't mixed up LDscores with genesets")


def download_files(args,main_file,ss_list,prefix,is_ldscore_main,is_ldscore_cond):

    """Download files for downstream analyses"""

    #Create folders
    logging.info('Creating folders')
    subprocess.call(['mkdir','/mnt/data/ss'])
    subprocess.call(['mkdir','/mnt/data/outld'])
    subprocess.call(['mkdir','/mnt/data/inld'])
    subprocess.call(['mkdir','/mnt/data/tmp'])

    # Download plink files
    logging.info('Downloading 1000 genomes plink files')
    subprocess.call(['gsutil','-m','cp','-r',args.tkg_plink_folder,'/mnt/data/'])

    # Downlad 1000 genome weights
    logging.info('Downloading 1000 genomes weights for ldscore')
    subprocess.call(['gsutil','-m','cp','-r',args.tkg_weights_folder,"/mnt/data/inld/"])

    # Downlad frequency files
    logging.info('Downloading 1000 genomes frequencies')
    subprocess.call(['gsutil','-m','cp','-r',args.tkg_freq_folder,"/mnt/data/"])


    # Download baseline
    if not args.no_baseline:
        logging.info('Downloading baseline annotation')
        subprocess.call(['gsutil','-m','cp','-r',args.baseline_ldscores_folder,"/mnt/data/inld/"])


    # Download main annotations
    if is_ldscore_main:   
        logging.info('Downloading main annotation LDscores(s):' + main_file)
        subprocess.call(['mkdir','/mnt/data/outld'])
        subprocess.call(['gsutil','-m','cp','-r',os.path.join(main_file, "") + '*' ,'/mnt/data/outld/'])
    else:
        logging.info('Downloading main annotation file(s):' + main_file)
        subprocess.call(['gsutil','cp',main_file,'/mnt/data/'])

    # Download conditional annotations
    if args.condition_annot:
        if (is_ldscore_cond and is_ldscore_cond is not None):
            logging.info('Downloading conditional ldscores annotation(s)')
            subprocess.call(['mkdir','/mnt/data/cond_ldscores'])
            cond_ld_list = args.condition_annot.split(',')
            for k in cond_ld_list:
                ts = os.path.join(random_string(7),"")
                subprocess.call(['mkdir','/mnt/data/cond_ldscores/' + ts])
                subprocess.call(['gsutil','-m','cp','-r',os.path.join(k, "") + '*' ,'/mnt/data/cond_ldscores/' + ts])
        else:
            logging.info('Downloading file(s) containing conditional annotations')
            subprocess.call(['mkdir','/mnt/data/outcondld'])
            cond_file_list = args.condition_annot.split(',')
            for k in cond_file_list:
                subprocess.call(['gsutil','cp',k,"/mnt/data/"])
	    
    # Dowload SNP-list for generating LD-scores
    logging.info('Downloading SNP list for LDscore')
    subprocess.call(['gsutil','cp',args.snp_list_file,'/mnt/data/list.txt'])

    # Download file mapping SNPs to positions
    logging.info('Downloading file to map genes to positions')
    subprocess.call(['gsutil','cp',args.gene_anno_pos_file,'/mnt/data/GENENAME_gene_annot.txt'])

    # Download summary stats
    logging.info('Downloading summary statistic(s):' + ':'.join(ss_list))
    for ss in ss_list:
        subprocess.call(['gsutil','cp',ss,'/mnt/data/ss/'])


def prepare_annotations(args,gene_list,outldscore,plink_panel,noun):

    """Prepare LDscores for analysis"""
    logging.info('Creating LDscores')

    for chrom in range(1, 23):

        logging.debug('Running genesets_to_ldscores.py for chr ' + str(chrom) + ' and geneset-file ' + str(gene_list))
        subprocess.call(['/home/sc_enrichement/sc_enrichement-master/genesets_to_ldscores.py',
                        '--geneset-file',gene_list,
                        '--gene-annot',"/mnt/data/GENENAME_gene_annot.txt",
                        '--bfile-chr',plink_panel,
                        '--ldscores_prefix','/mnt/data/tmp/temp_dscore',
                        '--windowsize',str(args.windowsize),
                        '--gene-col-name', str(args.gene_col_name),
                        '--chrom', str(chrom)])
        if 'binary' in noun:
            logging.debug('Running ldsc.py for chr ' + str(chrom) )
            subprocess.call(['/home/ldscore/ldsc-kt_exclude_files/ldsc.py',
                            '--l2',
                            '--bfile',plink_panel + str(chrom),
                            '--ld-wind-cm', "1",
                            '--annot','/mnt/data/tmp/temp_dscore.' + str(chrom) + '.annot.gz',
                            '--thin-annot',
                            '--out', outldscore + "." + str(chrom),
                            '--print-snps',"/mnt/data/list.txt"])
        elif (('continuous' in noun) and args.quantiles):
            try:
                logging.debug('Running ldsc.py for chr ' + str(chrom) )
                subprocess.call(['/home/ldscore/ldsc-kt_exclude_files/ldsc.py',
                                '--l2',
                                '--bfile',plink_panel + str(chrom),
                                '--ld-wind-cm', "1",
                                '--cont-bin','/mnt/data/tmp/temp_dscore.' + str(chrom) + '.cont_bin.gz',
                                '--cont-quantiles',str(args.quantiles),
                                '--thin-annot',
                                '--out', outldscore + "." + str(chrom)])
            except ValueError:
                sys.exit("The continuous annotation you've entered has non-unique quantile bin edges. Please use --cont-breaks flag instead with user specified bins.")    
        elif (('continuous' in noun) and args.cont_breaks):
            logging.debug('Running ldsc.py for chr ' + str(chrom) )
            subprocess.call(['/home/ldscore/ldsc-kt_exclude_files/ldsc.py',
                            '--l2',
                            '--bfile',plink_panel + str(chrom),
                            '--ld-wind-cm', "1",
                            '--cont-bin','/mnt/data/tmp/temp_dscore.' + str(chrom) + '.cont_bin.gz',
                            '--cont-breaks',args.cont_breaks,
                            '--thin-annot',
                            '--out', outldscore + "." + str(chrom)])
      

def commonprefix(m):

    """Given a list of pathnames, returns the longest common leading component"""

    if not m: return ''
    s1 = min(m)
    s2 = max(m)
    for i, c in enumerate(s1):
        if c != s2[i]:
            return s1[:i]
    return s1

def prepare_params_file(args,prefix,name_main_ldscore,params_file='/mnt/data/params.ldcts'):

    """ Save the parameter file containing the name of the ldscores to use for partitioning heritability """

    with open(params_file, 'w') as file:
        logging.debug('Save parameter file with prefix: ' + prefix + ' and ldscore: /mnt/data/outld/' + name_main_ldscore)
        file.write(prefix + "\t" + '/mnt/data/outld/' + name_main_ldscore + '\n')



def write_report(report_name,sum_stat,main_panel,cond_panels,outfile):

    """ Write a report about which ldscores panels have been used etc.. """

    with open(report_name, 'a') as file:
        file.write("Summary statistic(s) used: " + sum_stat + '\n')
        file.write("Main panel(s) used: " + main_panel + '\n')
        file.write("Conditional panel(s) used: " + cond_panels + '\n')
        file.write("Main output file(s): " + outfile + '\n')


def ldsc_h2(infile, phname, params_file, ld_ref_panel, ld_w_panel, tg_f_panel,outfile):

    """Perform partioning hertiability """

    subprocess.call(['/home/ldscore/ldsc-kt_exclude_files/ldsc.py',
                                '--h2-cts',infile,
                                '--ref-ld-chr',ld_ref_panel,
                                '--ref-ld-chr-cts',params_file,
                                '--w-ld-chr',ld_w_panel,
                                '--frqfile-chr',tg_f_panel,
                                '--overlap-annot',
                                '--print-all-cts',
                                '--print-coefficients',
                                '--out',outfile])

    logging.info('Running estimate_h2 on: ' + infile)


if __name__ == "__main__":

    args = parse_args()

    main_file = args.main_annot
    prefix = args.ldscores_prefix
    ss_list = args.summary_stats_files.split(',')
    if args.condition_annot:
        is_ldscore_cond = recognize_ldscore_genelist(args.condition_annot)  
    else:
        is_ldscore_cond=None 
    is_ldscore_main = recognize_ldscore_genelist(main_file)
    
    logging.info('The main annotation file(s) or LDscore(s) to Download: '+ main_file)
    logging.info('The summary statistic(s) to download: ' + ':'.join(ss_list))

    ld_ref_panel = "No Baseline Panel"
    ld_cond_panel = "No Conditional Panel"

    # Set up the ennviroment
    download_files(args,main_file,ss_list,prefix,is_ldscore_main,is_ldscore_cond)
    
    # 1000 genome files
    name_plink = os.path.split(args.tkg_plink_folder)
    name = glob.glob('/mnt/data/' + name_plink[-1] + "/*")
    plink_panel = commonprefix(name)
    logging.debug('plink_panel: ' + plink_panel)

    #Create annotations for main outcome (put each annotation in a different folder)
    #If it is an LDscore put it in a folder and get the name of the LDscore
    if not is_ldscore_main:
        noun = type_of_file('/mnt/data/' + os.path.basename(main_file))
        logging.info('The type of file that will be used in the analysis: '+noun)
        prepare_annotations(args,gene_list='/mnt/data/' + os.path.basename(main_file), outldscore='/mnt/data/outld/' + prefix , plink_panel=plink_panel,noun=noun)
        name_main_ldscore = prefix + '.'
    else:
        temp_name_list =  [os.path.basename(x) for x in glob.glob('/mnt/data/outld/*')]
        name_main_ldscore = commonprefix(temp_name_list)

	    
    # If provided, prepare annotation for conditioning gene lists
    if (not is_ldscore_cond and is_ldscore_cond is not None):
        cond_list = args.condition_annot.split(',')
        for k in cond_list:
            k_name = os.path.basename(k)
            noun = type_of_file('/mnt/data/' + k_name)
            subprocess.call(['mkdir','/mnt/data/outcondld/' + k_name])
            prepare_annotations(args,gene_list='/mnt/data/' + k_name,outldscore='/mnt/data/outcondld/' + k_name + '/' + k_name, plink_panel=plink_panel,noun=noun)

    # Save parameter file
    prepare_params_file(args,prefix,name_main_ldscore)

    # Weight panel
    name_w = os.path.split(args.tkg_weights_folder)
    name = glob.glob('/mnt/data/inld/' + name_w[-1] + "/*")
    ld_w_panel = commonprefix(name)
    logging.debug('ld_w_panel: ' + ld_w_panel)


    # Frequency panel
    name_f = os.path.split(args.tkg_freq_folder)
    name = glob.glob('/mnt/data/' + name_f[-1] + "/*")
    tg_f_panel = commonprefix(name)
    logging.debug('tg_f_panel: ' + tg_f_panel)

    # LDscore baseline panel
    if not args.no_baseline:
        name_ldref = os.path.split(args.baseline_ldscores_folder)
        name = glob.glob('/mnt/data/inld/' + name_ldref[-1] + "/*")
        ld_ref_panel = commonprefix(name)
        logging.debug('ld_ref_panel: ' + ld_ref_panel)

    # LDscore conditional panels
    if is_ldscore_cond:
        name_ldcond = glob.glob('/mnt/data/cond_ldscores/*')
        ld_cond_panels_t = []
        for folder in name_ldcond:
            ld_cond_panels_t.append(commonprefix(glob.glob(folder + '/*')))
        logging.debug('ld_cond_panels_t: ' + ':'.join(ld_cond_panels_t))

    # LDscore conditional panels (created from files)
    if not is_ldscore_cond:
        name_ldcond_file = glob.glob('/mnt/data/outcondld/*')
        ld_cond_panels_file_t = []
        for folder in name_ldcond_file:
            ld_cond_panels_file_t.append(commonprefix(glob.glob(folder + '/*')))
        logging.debug('ld_cond_panels_file_t: ' + ':'.join(ld_cond_panels_file_t))

    # Summary statistics
    list_sumstats_file=glob.glob("/mnt/data/ss/*")

    # Panels for conditioning
    if not args.no_baseline:
         ld_cond_panel = ld_ref_panel
         if is_ldscore_cond and is_ldscore_cond is not None:
            ld_cond_panel = ','.join(ld_cond_panels_t + [ld_ref_panel])
         if (is_ldscore_cond is not None and not is_ldscore_cond):
            ld_cond_panel = ','.join(ld_cond_panels_file_t + [ld_ref_panel])
    elif (args.no_baseline and args.condition_annot):
        if is_ldscore_cond:
            ld_cond_panel = ','.join(ld_cond_panels_t)
        if not is_ldscore_cond:
            ld_cond_panel = ','.join(ld_cond_panels_file_t)
    else:
        sys.exit("No baseline panel or conditional panel specified - Interrupting")

    logging.info('The following panel(s) will be used for conditioning: ' + ':'.join([ld_cond_panel]))

    # Partitioning heritability
    outfiles_list = []
    for sumstats in list_sumstats_file:
        phname = os.path.basename(sumstats).replace('.sumstats.gz','')
        outfile = '/mnt/data/' + phname + '.' + prefix + '.ldsc'
        outfiles_list.append('/mnt/data/' + phname + '.ldsc.cell_type_results.txt')
        logging.info('Running partition LDscores for ' + phname)
        ldsc_results = ldsc_h2(infile=sumstats, phname=phname, params_file='/mnt/data/params.ldcts',ld_ref_panel=ld_cond_panel, ld_w_panel=ld_w_panel,tg_f_panel=tg_f_panel,outfile=outfile)


    # Writing report
    write_report(report_name=prefix + '.report',sum_stat='\t'.join(ss_list),main_panel=main_file, cond_panels=ld_cond_panel, outfile='\t'.join(outfiles_list))

    if args.export_ldscore_path:
        logging.info('LDscores copied to ' + str(args.export_ldscore_path))
        subprocess.call(['gsutil','-m','cp','-r','/mnt/data/outld/*',os.path.join(args.export_ldscore_path,"")])
    
    # Writing the results
    logging.info('Results copied to ' + str(args.export_ldscore_path))
    subprocess.call(['gsutil','cp','/mnt/data/*.ldsc.cell_type_results.txt',os.path.join(args.out,"")])
    subprocess.call(['gsutil','cp',"_".join(prefix) + '.report',os.path.join(args.out,"")])

    logging.info('FINITO!')