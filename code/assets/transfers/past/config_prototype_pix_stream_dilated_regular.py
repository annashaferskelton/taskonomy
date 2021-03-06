from __future__ import absolute_import, division, print_function

import functools
import numpy as np
import os
import sys
import tensorflow as tf

sys.path.insert( 1, os.path.realpath( '../../models' ) )
sys.path.insert( 1, os.path.realpath( '../../lib' ) )

import data.load_ops as load_ops
from   data.load_ops import mask_if_channel_le
from   data.task_data_loading import load_and_preprocess_img, load_and_preprocess_img_fast, load_and_specify_preprocessors, create_input_placeholders_and_ops_transfer, load_target
from   general_utils import RuntimeDeterminedEnviromentVars
import general_utils
import models.architectures as architectures

from   models.gan_discriminators import pix2pix_discriminator
from   models.resnet_v1 import resnet_v1_50
from   models.sample_models import *
from   models.transfer_models import *
from   models.utils import leaky_relu

PRE_INPUT_TASK = "<PRE_INPUT_TASK>" # e.g. autoencoder
INPUT_TASK = "<INPUT_TASK>" # e.g. autoencoder
TARGET_TASK = "<TARGET_TASK>" # e.g. vanishing_point
NUM_LAYERS = int("<NUM_LAYERS>") # e.g. vanishing_point
KERNEL_SIZE = int("<KERNEL_SIZE>")
CFG_DIR = "final"
SAVE_TO_S3 = True



FLATTEN_TASKS = [
    'fix_pose', 
    'non_fixated_pose',
    'point_match',
    'ego_motion',
    'jigsaw',
    'vanishing_point'
]

def get_cfg( nopause=False ):
    root_dir = '/home/ubuntu/task-taxonomy-331b'
    cfg = {}

    representation_task = INPUT_TASK
    representation_dir = 'representations'
    transitive = False
    if PRE_INPUT_TASK and PRE_INPUT_TASK != "PRE_INPUT_TASK":
        representation_task = PRE_INPUT_TASK + '__' + INPUT_TASK + '__' +  '1024'
        representation_dir = 'representations_transfer_1024'
        transitive = True

    ### -----CHANGE HERE--------------------------
    cfg['config_dir_input'] = '/home/ubuntu/task-taxonomy-331b/experiments/{}/{}'.format(
        CFG_DIR,
        INPUT_TASK)
    cfg['config_dir_target'] = '/home/ubuntu/task-taxonomy-331b/experiments/{}/{}'.format(
        CFG_DIR,
        TARGET_TASK)
    ### -------------------------------------------    
    # Automatically populate data loading variables
    input_cfg = general_utils.load_config(cfg['config_dir_input'], nopause=True)
    cfg['input_cfg'] = input_cfg

    # Replace loading info with the version from the target config
    target_cfg = general_utils.load_config(cfg['config_dir_target'], nopause=True)
    cfg['target_cfg'] = target_cfg
    general_utils.update_keys(cfg, "input", target_cfg)
    general_utils.update_keys(cfg, "target", target_cfg)
    general_utils.update_keys(cfg, "is_discriminative", target_cfg)
    general_utils.update_keys(cfg, "num_input", target_cfg)
    general_utils.update_keys(cfg, "single_filename_to_multiple", target_cfg)
    general_utils.update_keys(cfg, "preprocess_fn", target_cfg)
    general_utils.update_keys(cfg, "mask_by_target_func", target_cfg)
    general_utils.update_keys(cfg, "depth_mask", target_cfg)
    general_utils.update_keys(cfg, "mask_fn", target_cfg)
    general_utils.update_keys(cfg, "find_target_in_config", target_cfg)

    # For segmentation
    general_utils.update_keys(cfg, "num_pixels", target_cfg)
    general_utils.update_keys(cfg, "only_target_discriminative", target_cfg)
    general_utils.update_keys(cfg, "num_pixels", target_cfg)
    

    # kludge where we list all of the files
    # cfg['list_of_fileinfos'] = os.path.abspath( os.path.join( root_dir, 'assets/aws_data/train_image_split_0.npy') )    
    cfg['train_list_of_fileinfos'] = os.path.abspath( os.path.join( root_dir, 'assets/aws_data/val_image_split_0.npy') )    
    cfg['val_list_of_fileinfos'] = os.path.abspath( os.path.join( root_dir, 'assets/aws_data/test_image_split_0.npy') )    
    
    # Define where the extracted representations are stored
    cfg['train_representations_file'] = os.path.join(
        input_cfg['log_root'], representation_task,
        '{task}_{train_split}_representations.pkl'.format(
            task=representation_task,
            train_split='train' if transitive else 'val'))
    cfg['val_representations_file'] = os.path.join(
        input_cfg['log_root'], representation_task,
        '{task}_{train_split}_representations.pkl'.format(
            task=representation_task,
            train_split='val' if transitive else 'test'))
    
    # Now use 'val' for training and 'test' for validation... :(
    tmp = target_cfg['train_filenames']
    target_cfg['train_filenames'] = str(target_cfg['val_filenames'])
    target_cfg['val_filenames'] = str(target_cfg['test_filenames'])
    target_cfg['test_filenames'] = None # str(tmp)
    cfg['finetune_decoder'] = ("<FINETUNE_DECODER>" == "True")

    general_utils.update_keys(cfg, "train_filenames", target_cfg)
    general_utils.update_keys(cfg, "val_filenames", target_cfg)
    general_utils.update_keys(cfg, "test_filenames", target_cfg)
    general_utils.update_keys(cfg, "dataset_dir", target_cfg)
    
    # Where the target decoder is stored
    cfg['model_path'] = '{}/{}/model.permanent-ckpt'.format(target_cfg['log_root'], TARGET_TASK)

    # Params for training
    cfg['root_dir'] = root_dir
    # cfg['num_epochs'] = 30 if INPUT_TASK == 'random' else 4
    cfg['num_epochs'] = 15
    cfg['max_ckpts_to_keep'] = cfg['num_epochs']
    cfg['target_model_type'] = target_cfg['model_type']

    cfg['weight_decay'] = 1e-6  # 1e-7, 1
    cfg['model_type'] = architectures.TransferNet

    ## DATA LOADING
    # representations
    cfg['representation_dim'] = (16, 16, 8)
    cfg['representation_dtype'] = tf.float32

    ## SETUP MODEL
    # Transfer
    cfg['encoder'] = transfer_two_stream_with_bn_ends 
    # if INPUT_TASK == 'random':
        # cfg['encoder'] = transfer_multilayer_conv_with_bn_ends
    cfg['hidden_size'] = int("<HIDDEN_SIZE>") # This will be the number of interior channels
    cfg['encoder_kwargs'] = {
        'side_encoder_func' : side_encoder_with_dilated_conv,
        'output_channels': cfg['representation_dim'][-1],
        'kernel_size': [KERNEL_SIZE, KERNEL_SIZE],
        'stride': 1,
        'batch_norm_epsilon': 1e-5,
        'batch_norm_decay': 0.8, #0.95
        'weight_decay': cfg['weight_decay'],
        'num_layers': int(NUM_LAYERS),
        'flatten_output': 'flatten' in target_cfg['encoder_kwargs']
        # 'flatten_output': (TARGET_TASK in FLATTEN_TASKS)
    }

    # learning
    general_utils.update_keys(cfg, "initial_learning_rate", target_cfg)
    general_utils.update_keys(cfg, "optimizer", target_cfg)
    general_utils.update_keys(cfg, "clip_norm", target_cfg)
    def pwc(initial_lr, **kwargs):
        global_step = kwargs['global_step']
        del kwargs['global_step']
        return tf.train.piecewise_constant(global_step, **kwargs)
    cfg['learning_rate_schedule'] = pwc
    cfg['learning_rate_schedule_kwargs' ] = {
        'boundaries': [np.int64(0), np.int64(5000)], # need to be int64 since global step is...
        'values': [cfg['initial_learning_rate'], cfg['initial_learning_rate']/10]
    }

    # cfg['initial_learning_rate'] = 1e-4  # 1e-6, 1e-1
    # cfg[ 'optimizer' ] = tf.train.AdamOptimizer
    # cfg[ 'optimizer_kwargs' ] = {}

    ## LOCATIONS
    # logging
    config_dir = os.path.dirname(os.path.realpath( __file__ ))
    task_name = os.path.basename( config_dir )
    if transitive:
        if SAVE_TO_S3:
            log_root = '/home/ubuntu/s3/experiment_models/pix_stream_transfer_transitive_{}/dilated_regular/{}/{}'.format(cfg['hidden_size'], INPUT_TASK, TARGET_TASK)
        else:
            log_root = '/home/ubuntu/experiment_models/pix_stream_transfer_transitive_{}/dilated_regular/{}/{}'.format(cfg['hidden_size'], INPUT_TASK, TARGET_TASK)
    else:
        if SAVE_TO_S3:
            log_root = '/home/ubuntu/s3/experiment_models/pix_stream_transfer_{}/dilated_regular/{}/{}'.format(cfg['hidden_size'], INPUT_TASK, TARGET_TASK)
        else:
            log_root = '/home/ubuntu/experiment_models/pix_stream_transfer_{}/dilated_regular/{}/{}'.format(cfg['hidden_size'], INPUT_TASK, TARGET_TASK)
    cfg['log_root'] = log_root
    cfg['log_dir'] = os.path.join(log_root, 'logs')

    # input pipeline
    data_dir = '/home/ubuntu/s3'
    cfg['meta_file_dir'] = 'assets/aws_data'
    cfg['create_input_placeholders_and_ops_fn'] = create_input_placeholders_and_ops_transfer
    cfg['randomize'] = True 
    cfg['num_read_threads'] = 300
    cfg['batch_size'] = 32
    cfg['inputs_queue_capacity'] = 4096  
    

    # Checkpoints and summaries
    cfg['summary_save_every_secs'] = 600
    cfg['checkpoint_save_every_secs'] = 3000 

    RuntimeDeterminedEnviromentVars.register_dict( cfg )  # These will be loaded at runtime
    print_cfg( cfg, nopause=nopause )
   
    return cfg

def print_cfg( cfg, nopause=False ):
    print('-------------------------------------------------')
    print('config:')
    template = '\t{0:30}{1}'
    for key in sorted( cfg.keys() ):
        print(template.format(key, cfg[key]))
    print('-------------------------------------------------')
    
    if nopause:
        return
    raw_input('Press Enter to continue...')
    print('-------------------------------------------------')
