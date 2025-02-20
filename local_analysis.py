##### MODEL AND DATA LOADING
import datetime
import torch
import torch.utils.data
import torchvision.transforms as transforms
import torchvision.datasets as datasets
from torch.autograd import Variable
import numpy as np
import matplotlib.pyplot as plt
import cv2
from PIL import Image

import re

import os
import copy

from DeformableProtoPNet.helpers import makedir, find_high_activation_crop
import DeformableProtoPNet.train_and_test as tnt

from DeformableProtoPNet.log import create_logger
from DeformableProtoPNet.preprocess import mean, std, undo_preprocess_input_function
from DeformableProtoPNet.push import get_deformation_info

import argparse

from logger import WandbLogger

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('-gpuid', nargs=1, type=str, default='0')
    args = parser.parse_args()

    prototype_layer_stride = 1

    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpuid[0]

     # specify the test image to be analyzed
    test_image_dir = './test_images/' #'./local_analysis/Painted_Bunting_Class15_0081/'
    test_image_name = 'DME-15208-1.jpeg' #'Painted_Bunting_0081_15230.jpg'
    test_image_label = 1 #15

    test_image_path = os.path.join(test_image_dir, test_image_name)

    # load the model
    check_test_accu = True

    load_model_dir = './saved_models/densenet121/2/' #'./saved_models/vgg19/003/'
    load_model_name = '80push0.9660.pth' #'10_18push0.7822.pth'

    model_base_architecture = load_model_dir.split('/')[2]
    experiment_run = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    root_save_analysis_path = os.path.join('./saved_visualizations', experiment_run)
    makedir(root_save_analysis_path)

    log, logclose = create_logger(log_filename=os.path.join(root_save_analysis_path, 'local_analysis.log'))
    wandb_logger = WandbLogger(
        {}, logger_name='DeProtoPNet_Test', project='FinalProject')

    load_model_path = os.path.join(load_model_dir, load_model_name)
    epoch_number_str = re.search(r'\d+', load_model_name).group(0)
    start_epoch_number = int(epoch_number_str)
    if start_epoch_number == 0:
        start_epoch_number = 30
        epoch_number_str = '30'

    log('load model from ' + load_model_path)
    log('model base architecture: ' + model_base_architecture)
    log('experiment run: ' + experiment_run)
    log('epoch number: ' + str(start_epoch_number))

    ppnet = torch.load(load_model_path)
    ppnet = ppnet.cuda()
    ppnet_multi = torch.nn.DataParallel(ppnet)

    img_size = ppnet_multi.module.img_size
    prototype_shape = ppnet.prototype_shape

    class_specific = True

    normalize = transforms.Normalize(mean=mean,
                                    std=std)

    # load the test data and check test accuracy
    from config import test_dir
    if "stanford_dogs" in load_model_path:
        test_dir = './datasets/stanford_dogs/test/'
    if check_test_accu:
        test_batch_size = 100

        test_dataset = datasets.ImageFolder(
            test_dir,
            transforms.Compose([
                transforms.Resize(size=(img_size, img_size)),
                transforms.Lambda(lambda img: img.convert("RGB")),
                transforms.ToTensor(),
                normalize,
            ]))
        test_loader = torch.utils.data.DataLoader(
            test_dataset, batch_size=test_batch_size, shuffle=True,
            num_workers=4, pin_memory=False)
        log('test set size: {0}'.format(len(test_loader.dataset)))

        accu = tnt.test(model=ppnet_multi, dataloader=test_loader,
                        class_specific=class_specific, log=print, wandb_logger=wandb_logger)

    ##### SANITY CHECK
    # confirm prototype class identity
    load_img_dir = os.path.join(load_model_dir, 'img')

    prototype_info = np.load(os.path.join(load_img_dir, 'epoch-'+epoch_number_str, 'bb'+epoch_number_str+'.npy'))
    prototype_img_identity = prototype_info[:, -1]

    log('Prototypes are chosen from ' + str(len(set(prototype_img_identity))) + ' number of classes.')
    log('Their class identities are: ' + str(prototype_img_identity))

    # confirm prototype connects most strongly to its own class
    prototype_max_connection = torch.argmax(ppnet.last_layer.weight, dim=0)
    prototype_max_connection = prototype_max_connection.cpu().numpy()
    if np.sum(prototype_max_connection == prototype_img_identity) == ppnet.num_prototypes:
        log('All prototypes connect most strongly to their respective classes.')
    else:
        log('WARNING: Not all prototypes connect most strongly to their respective classes.')
            
    ##### HELPER FUNCTIONS FOR PLOTTING
    def save_preprocessed_img(fname, preprocessed_imgs, index=0):
        img_copy = copy.deepcopy(preprocessed_imgs[index:index+1])
        undo_preprocessed_img = undo_preprocess_input_function(img_copy)
        print('image index {0} in batch'.format(index))
        undo_preprocessed_img = undo_preprocessed_img[0]
        undo_preprocessed_img = undo_preprocessed_img.detach().cpu().numpy()
        undo_preprocessed_img = np.transpose(undo_preprocessed_img, [1,2,0])
        
        plt.imsave(fname, undo_preprocessed_img)
        return undo_preprocessed_img

    def save_prototype(fname, epoch, index):
        try:
            p_img = plt.imread(os.path.join(load_img_dir, 'prototype-img'+str(index)+'.png'))
            plt.imsave(fname, p_img)
        except:
            print("Problem loading ", os.path.join(load_img_dir, 'prototype-img'+str(index)+'.png'))

    def save_prototype_box(fname, epoch, index):
        try:
            p_img = plt.imread(os.path.join(load_img_dir, 'prototype-img-with_box'+str(index)+'.png'))
            plt.imsave(fname, p_img)
        except:
            print("Problem loading ", os.path.join(load_img_dir, 'prototype-img-with_box'+str(index)+'.png'))

    def imsave_with_bbox(fname, img_rgb, bbox_height_start, bbox_height_end,
                        bbox_width_start, bbox_width_end, color=(0, 255, 255)):
        try:
            img_bgr_uint8 = cv2.cvtColor(np.uint8(255*img_rgb), cv2.COLOR_RGB2BGR)
            cv2.rectangle(img_bgr_uint8, (bbox_width_start, bbox_height_start), (bbox_width_end-1, bbox_height_end-1),
                        color, thickness=2)
            img_rgb_uint8 = img_bgr_uint8[...,::-1]
            img_rgb_float = np.float32(img_rgb_uint8) / 255
            plt.imsave(fname, img_rgb_float)
        except:
            print("Problem loading: imsave with bbox")

    def save_deform_info(model, offsets, input, activations, 
                        save_dir,
                        prototype_img_filename_prefix,
                        proto_index):
        prototype_shape = model.prototype_shape
        if not hasattr(model, "prototype_dilation"):
            dilation = model.prototype_dillation
        else:
            dilation = model.prototype_dilation
        original_img_size = input.shape[0]

        colors = [(230/255, 25/255, 75/255), (60/255, 180/255, 75/255), (255/255, 225/255, 25/255),
                                    (0, 130/255, 200/255), (245/255, 130/255, 48/255), (70/255, 240/255, 240/255),
                                    (240/255, 50/255, 230/255), (170/255, 110/255, 40/255), (0,0,0)]
        argmax_proto_act_j = \
                    list(np.unravel_index(np.argmax(activations, axis=None),
                                        activations.shape))
        fmap_height_start_index = argmax_proto_act_j[0] * prototype_layer_stride
        fmap_width_start_index = argmax_proto_act_j[1] * prototype_layer_stride

        original_img_j_with_boxes = input.copy()

        num_def_groups = 1#model.num_prototypes // model.num_classes
        def_grp_index = proto_index % num_def_groups
        def_grp_offset = def_grp_index * 2 * prototype_shape[-2] * prototype_shape[-1]

        for i in range(prototype_shape[-2]):
            for k in range(prototype_shape[-1]):
                # offsets go in order height offset, width offset
                h_index = def_grp_offset + 2 * (k + prototype_shape[-2]*i)
                w_index = h_index + 1
                h_offset = offsets[0, h_index, fmap_height_start_index, fmap_width_start_index]
                w_offset = offsets[0, w_index, fmap_height_start_index, fmap_width_start_index]

                # Subtract prototype_shape // 2 because fmap start indices give the center location, and we start with top left
                def_latent_space_row = fmap_height_start_index + h_offset + (i - prototype_shape[-2] // 2) * dilation[0]
                def_latent_space_col = fmap_width_start_index + w_offset + (k - prototype_shape[-1] // 2)* dilation[1]

                def_image_space_row_start = int(def_latent_space_row * original_img_size / activations.shape[-2])
                def_image_space_row_end = int((1 + def_latent_space_row) * original_img_size / activations.shape[-2])
                def_image_space_col_start = int(def_latent_space_col * original_img_size / activations.shape[-1])
                def_image_space_col_end = int((1 + def_latent_space_col) * original_img_size / activations.shape[-1])
    
                img_with_just_this_box = input.copy()
                cv2.rectangle(img_with_just_this_box,(def_image_space_col_start, def_image_space_row_start),
                                                        (def_image_space_col_end, def_image_space_row_end),
                                                        colors[i*prototype_shape[-1] + k],
                                                        1)
                plt.imsave(os.path.join(save_dir,
                                prototype_img_filename_prefix + str(proto_index) + '_patch_' + str(i*prototype_shape[-1] + k) + '-with_box.png'),
                    img_with_just_this_box,
                    vmin=0.0,
                    vmax=1.0)

                cv2.rectangle(original_img_j_with_boxes,(def_image_space_col_start, def_image_space_row_start),
                                                        (def_image_space_col_end, def_image_space_row_end),
                                                        colors[i*prototype_shape[-1] + k],
                                                        1)
                
                if not (def_image_space_col_start < 0 
                    or def_image_space_row_start < 0
                    or def_image_space_col_end >= input.shape[0]
                    or def_image_space_row_end >= input.shape[1]):
                    plt.imsave(os.path.join(save_dir,
                                    prototype_img_filename_prefix + str(proto_index) + '_patch_' + str(i*prototype_shape[-1] + k) + '.png'),
                        input[def_image_space_row_start:def_image_space_row_end, def_image_space_col_start:def_image_space_col_end, :],
                        vmin=0.0,
                        vmax=1.0)
                
        plt.imsave(os.path.join(save_dir,
                                prototype_img_filename_prefix + str(proto_index) + '-with_box.png'),
                    original_img_j_with_boxes,
                    vmin=0.0,
                    vmax=1.0)

    # load the test image and forward it through the network
    preprocess = transforms.Compose([
    transforms.Resize((img_size,img_size)),
    transforms.Lambda(lambda img: img.convert("RGB")),
    transforms.ToTensor(),
    normalize
    ])

    for test_image_name in os.listdir(test_image_dir):
        if not test_image_name.lower().endswith(".jpeg") or test_image_name.lower().endswith(".jpg"):
            continue
        save_analysis_path = os.path.join(root_save_analysis_path, os.path.splitext(test_image_name)[0])
        makedir(save_analysis_path)

        test_image_path = os.path.join(test_image_dir, test_image_name)


        img_pil = Image.open(test_image_path)
        img_tensor = preprocess(img_pil)
        img_variable = Variable(img_tensor.unsqueeze(0))

        images_test = img_variable.cuda()
        test_image_label = test_dataset.class_to_idx[test_image_name.split("-")[0]]
        labels_test = torch.tensor([test_image_label])

        logits, additional_returns = ppnet_multi(images_test)
        prototype_activations = additional_returns[3]
        conv_output, prototype_activation_patterns = ppnet.push_forward(images_test)

        offsets, _ = get_deformation_info(conv_output, ppnet_multi)
        offsets = offsets.detach()

        tables = []
        for i in range(logits.size(0)):
            tables.append((torch.argmax(logits, dim=1)[i].item(), labels_test[i].item()))
            log(str(i) + ' ' + str(tables[-1]))

        idx = 0
        predicted_cls = tables[idx][0]
        correct_cls = tables[idx][1]
        log(test_image_name)
        log('Predicted: ' + str(predicted_cls))
        log('Actual: ' + str(correct_cls))
        original_img = save_preprocessed_img(os.path.join(save_analysis_path, 'original_img.png'),
                                            images_test, idx)

        ##### MOST ACTIVATED (NEAREST) 10 PROTOTYPES OF THIS IMAGE
        makedir(os.path.join(save_analysis_path, 'most_activated_prototypes'))

        log('Most activated 10 prototypes of this image:')
        array_act, sorted_indices_act = torch.sort(prototype_activations[idx])
        for i in range(1,11):
            log('top {0} activated prototype for this image:'.format(i))
            save_prototype(os.path.join(save_analysis_path, 'most_activated_prototypes',
                                        'top-%d_activated_prototype.png' % i),
                        start_epoch_number, sorted_indices_act[-i].item())
            save_prototype_box(os.path.join(save_analysis_path, 'most_activated_prototypes',
                                        'top-%d_activated_prototype_with_box.png' % i),
                        start_epoch_number, sorted_indices_act[-i].item())
            log('prototype index: {0}'.format(sorted_indices_act[-i].item()))
            log('prototype class identity: {0}'.format(prototype_img_identity[sorted_indices_act[-i].item()]))
            if prototype_max_connection[sorted_indices_act[-i].item()] != prototype_img_identity[sorted_indices_act[-i].item()]:
                log('prototype connection identity: {0}'.format(prototype_max_connection[sorted_indices_act[-i].item()]))
            log('activation value (similarity score): {0}'.format(array_act[-i]))
            log('last layer connection with predicted class: {0}'.format(ppnet.last_layer.weight[predicted_cls][sorted_indices_act[-i].item()]))
            
            activation_pattern = prototype_activation_patterns[idx][sorted_indices_act[-i].item()].detach().cpu().numpy()
            upsampled_activation_pattern = cv2.resize(activation_pattern, dsize=(img_size, img_size),
                                                    interpolation=cv2.INTER_CUBIC)
            

            save_deform_info(model=ppnet, offsets=offsets, 
                                input=original_img, activations=activation_pattern,
                                save_dir=os.path.join(save_analysis_path, 'most_activated_prototypes'),
                                prototype_img_filename_prefix='top-%d_activated_prototype_' % i,
                                proto_index=sorted_indices_act[-i].item())

            # show the most highly activated patch of the image by this prototype
            high_act_patch_indices = find_high_activation_crop(upsampled_activation_pattern)
            high_act_patch = original_img[high_act_patch_indices[0]:high_act_patch_indices[1],
                                        high_act_patch_indices[2]:high_act_patch_indices[3], :]
            log('most highly activated patch of the chosen image by this prototype:')
            plt.imsave(os.path.join(save_analysis_path, 'most_activated_prototypes',
                                    'most_highly_activated_patch_by_top-%d_prototype.png' % i),
                    high_act_patch)
            log('most highly activated patch by this prototype shown in the original image:')
            imsave_with_bbox(fname=os.path.join(save_analysis_path, 'most_activated_prototypes',
                                    'most_highly_activated_patch_in_original_img_by_top-%d_prototype.png' % i),
                            img_rgb=original_img,
                            bbox_height_start=high_act_patch_indices[0],
                            bbox_height_end=high_act_patch_indices[1],
                            bbox_width_start=high_act_patch_indices[2],
                            bbox_width_end=high_act_patch_indices[3], color=(0, 255, 255))
            
            # show the image overlayed with prototype activation map
            rescaled_activation_pattern = upsampled_activation_pattern - np.amin(upsampled_activation_pattern)
            rescaled_activation_pattern = rescaled_activation_pattern / np.amax(rescaled_activation_pattern)
            heatmap = cv2.applyColorMap(np.uint8(255*rescaled_activation_pattern), cv2.COLORMAP_JET)
            heatmap = np.float32(heatmap) / 255
            heatmap = heatmap[...,::-1]
            overlayed_img = 0.5 * original_img + 0.3 * heatmap
            log('prototype activation map of the chosen image:')
            #plt.axis('off')
            plt.imsave(os.path.join(save_analysis_path, 'most_activated_prototypes',
                                    'prototype_activation_map_by_top-%d_prototype.png' % i),
                    overlayed_img)
            log('--------------------------------------------------------------')

        ##### PROTOTYPES FROM TOP-k CLASSES
        k = 2
        log('Prototypes from top-%d classes:' % k)
        topk_logits, topk_classes = torch.topk(logits[idx], k=k)
        for i,c in enumerate(topk_classes.detach().cpu().numpy()):
            makedir(os.path.join(save_analysis_path, 'top-%d_class_prototypes' % (i+1)))

            log('top %d predicted class: %d' % (i+1, c))
            log('logit of the class: %f' % topk_logits[i])
            class_prototype_indices = np.nonzero(ppnet.prototype_class_identity.detach().cpu().numpy()[:, c])[0]
            class_prototype_activations = prototype_activations[idx][class_prototype_indices]
            _, sorted_indices_cls_act = torch.sort(class_prototype_activations)

            prototype_cnt = 1
            for j in reversed(sorted_indices_cls_act.detach().cpu().numpy()):
                prototype_index = class_prototype_indices[j]

                save_prototype_box(os.path.join(save_analysis_path, 'top-%d_class_prototypes' % (i+1),
                                            'top-%d_activated_prototype_with_box.png' % prototype_cnt),
                            start_epoch_number, prototype_index)
                log('prototype index: {0}'.format(prototype_index))
                log('prototype class identity: {0}'.format(prototype_img_identity[prototype_index]))
                if prototype_max_connection[prototype_index] != prototype_img_identity[prototype_index]:
                    log('prototype connection identity: {0}'.format(prototype_max_connection[prototype_index]))
                log('activation value (similarity score): {0}'.format(prototype_activations[idx][prototype_index]))
                log('last layer connection: {0}'.format(ppnet.last_layer.weight[c][prototype_index]))
                
                activation_pattern = prototype_activation_patterns[idx][prototype_index].detach().cpu().numpy()
                upsampled_activation_pattern = cv2.resize(activation_pattern, dsize=(img_size, img_size),
                                                        interpolation=cv2.INTER_CUBIC)

                save_deform_info(model=ppnet, offsets=offsets, 
                                input=original_img, activations=activation_pattern,
                                save_dir=os.path.join(save_analysis_path, 'top-%d_class_prototypes' % (i+1)),
                                prototype_img_filename_prefix='top-%d_activated_prototype_' % prototype_cnt,
                                proto_index=prototype_index)
                
                # show the most highly activated patch of the image by this prototype
                high_act_patch_indices = find_high_activation_crop(upsampled_activation_pattern)
                high_act_patch = original_img[high_act_patch_indices[0]:high_act_patch_indices[1],
                                            high_act_patch_indices[2]:high_act_patch_indices[3], :]
                log('most highly activated patch of the chosen image by this prototype:')
                plt.imsave(os.path.join(save_analysis_path, 'top-%d_class_prototypes' % (i+1),
                                        'most_highly_activated_patch_by_top-%d_prototype.png' % prototype_cnt),
                        high_act_patch)
                log('most highly activated patch by this prototype shown in the original image:')
                imsave_with_bbox(fname=os.path.join(save_analysis_path, 'top-%d_class_prototypes' % (i+1),
                                                    'most_highly_activated_patch_in_original_img_by_top-%d_prototype.png' % prototype_cnt),
                                img_rgb=original_img,
                                bbox_height_start=high_act_patch_indices[0],
                                bbox_height_end=high_act_patch_indices[1],
                                bbox_width_start=high_act_patch_indices[2],
                                bbox_width_end=high_act_patch_indices[3], color=(0, 255, 255))
                
                # show the image overlayed with prototype activation map
                rescaled_activation_pattern = upsampled_activation_pattern - np.amin(upsampled_activation_pattern)
                rescaled_activation_pattern = rescaled_activation_pattern / np.amax(rescaled_activation_pattern)
                heatmap = cv2.applyColorMap(np.uint8(255*rescaled_activation_pattern), cv2.COLORMAP_JET)
                heatmap = np.float32(heatmap) / 255
                heatmap = heatmap[...,::-1]
                overlayed_img = 0.5 * original_img + 0.3 * heatmap
                log('prototype activation map of the chosen image:')
                plt.imsave(os.path.join(save_analysis_path, 'top-%d_class_prototypes' % (i+1),
                                        'prototype_activation_map_by_top-%d_prototype.png' % prototype_cnt),
                        overlayed_img)
                log('--------------------------------------------------------------')
                prototype_cnt += 1
            log('***************************************************************')

        if predicted_cls == correct_cls:
            log('Prediction is correct.')
        else:
            log('Prediction is wrong.')

    logclose()

if __name__ == "__main__":
    main()