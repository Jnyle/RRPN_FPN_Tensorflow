# -*- coding:utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import tensorflow as tf
import numpy as np
import cv2

import os, sys
import time
sys.path.insert(0, '../')

from data.io import image_preprocess
from help_utils import help_utils
from libs.networks.network_factory import get_network_byname

from libs.rpn import build_rpn
from libs.fast_rcnn import build_fast_rcnn

from libs.configs import cfgs
from tools import restore_model

os.environ["CUDA_VISIBLE_DEVICES"] = cfgs.GPU_GROUP


def get_imgs():
    root_dir = cfgs.INFERENCE_IMAGE_PATH
    img_name_list = os.listdir(root_dir)
    img_list = [cv2.imread(os.path.join(root_dir, img_name))
                for img_name in img_name_list]
    return zip(img_name_list, img_list)


def inference():
    with tf.Graph().as_default():

        img_plac = tf.placeholder(shape=[None, None, 3], dtype=tf.uint8)

        img_tensor = tf.cast(img_plac, tf.float32) - tf.constant([103.939, 116.779, 123.68])
        img_batch = image_preprocess.short_side_resize_for_inference_data(img_tensor,
                                                                          target_shortside_len=cfgs.SHORT_SIDE_LEN)

        # ***********************************************************************************************
        # *                                         share net                                           *
        # ***********************************************************************************************
        _, share_net = get_network_byname(net_name=cfgs.NET_NAME,
                                          inputs=img_batch,
                                          num_classes=None,
                                          is_training=True,
                                          output_stride=None,
                                          global_pool=False,
                                          spatial_squeeze=False)
        # ***********************************************************************************************
        # *                                            RPN                                              *
        # ***********************************************************************************************
        rpn = build_rpn.RPN(net_name=cfgs.NET_NAME,
                            inputs=img_batch,
                            gtboxes_and_label=None,
                            is_training=False,
                            share_head=False,
                            share_net=share_net,
                            anchor_ratios=cfgs.ANCHOR_RATIOS,
                            anchor_scales=cfgs.ANCHOR_SCALES,
                            anchor_angles=cfgs.ANCHOR_ANGLES,
                            scale_factors=cfgs.SCALE_FACTORS,
                            base_anchor_size_list=cfgs.BASE_ANCHOR_SIZE_LIST,  # P2, P3, P4, P5, P6
                            level=cfgs.LEVEL,
                            anchor_stride=cfgs.ANCHOR_STRIDE,
                            top_k_nms=cfgs.RPN_TOP_K_NMS,
                            kernel_size=cfgs.KERNEL_SIZE,
                            use_angles_condition=True,
                            anchor_angle_threshold=cfgs.RPN_ANCHOR_ANGLES_THRESHOLD,
                            nms_angle_threshold=cfgs.RPN_NMS_ANGLES_THRESHOLD,
                            rpn_nms_iou_threshold=cfgs.RPN_NMS_IOU_THRESHOLD,
                            max_proposals_num=cfgs.MAX_PROPOSAL_NUM,
                            rpn_iou_positive_threshold=cfgs.RPN_IOU_POSITIVE_THRESHOLD,
                            rpn_iou_negative_threshold=cfgs.RPN_IOU_NEGATIVE_THRESHOLD,
                            rpn_mini_batch_size=cfgs.RPN_MINIBATCH_SIZE,
                            rpn_positives_ratio=cfgs.RPN_POSITIVE_RATE,
                            remove_outside_anchors=False,  # whether remove anchors outside
                            rpn_weight_decay=cfgs.WEIGHT_DECAY[cfgs.NET_NAME],
                            scope='')

        # rpn predict proposals
        rpn_proposals_boxes, rpn_proposals_scores = rpn.rpn_proposals()  # rpn_score shape: [300, ]

        # ***********************************************************************************************
        # *                                         Fast RCNN                                           *
        # ***********************************************************************************************
        fast_rcnn = build_fast_rcnn.FastRCNN(img_batch=img_batch,
                                             feature_pyramid=rpn.feature_pyramid,
                                             rpn_proposals_boxes=rpn_proposals_boxes,
                                             rpn_proposals_scores=rpn_proposals_scores,
                                             stop_gradient_for_proposals=False,
                                             img_shape=tf.shape(img_batch),
                                             roi_size=cfgs.ROI_SIZE,
                                             roi_pool_kernel_size=cfgs.ROI_POOL_KERNEL_SIZE,
                                             scale_factors=cfgs.SCALE_FACTORS,
                                             gtboxes_and_label=None,
                                             fast_rcnn_nms_iou_threshold=cfgs.FAST_RCNN_NMS_IOU_THRESHOLD,
                                             top_k_nms=cfgs.FAST_RCNN_TOP_K_NMS,
                                             nms_angle_threshold=cfgs.FAST_RCNN_NMS_ANGLES_THRESHOLD,
                                             use_angle_condition=False,
                                             level=cfgs.LEVEL,
                                             fast_rcnn_maximum_boxes_per_img=100,
                                             fast_rcnn_nms_max_boxes_per_class=cfgs.FAST_RCNN_NMS_MAX_BOXES_PER_CLASS,
                                             show_detections_score_threshold=cfgs.FINAL_SCORE_THRESHOLD,
                                             # show detections which score >= 0.6
                                             num_classes=cfgs.CLASS_NUM,
                                             fast_rcnn_minibatch_size=cfgs.FAST_RCNN_MINIBATCH_SIZE,
                                             fast_rcnn_positives_ratio=cfgs.FAST_RCNN_POSITIVE_RATE,
                                             fast_rcnn_positives_iou_threshold=cfgs.FAST_RCNN_IOU_POSITIVE_THRESHOLD,
                                             boxes_angle_threshold=cfgs.FAST_RCNN_BOXES_ANGLES_THRESHOLD,
                                             use_dropout=cfgs.USE_DROPOUT,
                                             weight_decay=cfgs.WEIGHT_DECAY[cfgs.NET_NAME],
                                             is_training=False)

        fast_rcnn_decode_boxes, fast_rcnn_score, num_of_objects, detection_category = \
            fast_rcnn.fast_rcnn_predict()

        # test
        init_op = tf.group(
            tf.global_variables_initializer(),
            tf.local_variables_initializer()
        )

        restorer, restore_ckpt = restore_model.get_restorer()

        config = tf.ConfigProto()
        # config.gpu_options.per_process_gpu_memory_fraction = 0.5
        config.gpu_options.allow_growth = True
        with tf.Session(config=config) as sess:
            sess.run(init_op)
            if not restorer is None:
                restorer.restore(sess, restore_ckpt)
                print('restore model')

            coord = tf.train.Coordinator()
            threads = tf.train.start_queue_runners(sess, coord)

            for img_name, a_img in get_imgs():
                start = time.time()

                _fast_rcnn_decode_boxes, _fast_rcnn_score, _detection_category \
                    = sess.run([fast_rcnn_decode_boxes, fast_rcnn_score, detection_category],
                               feed_dict={img_plac: a_img})
                end = time.time()
                print("{} cost time : {} ".format(img_name, end-start))
                final_detections = help_utils.draw_box_cv(np.array(a_img, dtype=np.float32)-np.array([103.939, 116.779, 123.68]),
                                                        boxes=_fast_rcnn_decode_boxes,
                                                        labels=_detection_category,
                                                        scores=_fast_rcnn_score)

                cv2.imwrite(cfgs.INFERENCE_SAVE_PATH + '/' + img_name,
                            final_detections)



            coord.request_stop()
            coord.join(threads)

if __name__ == '__main__':
    inference()