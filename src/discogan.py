# ---------------------------------------------------------
# Tensorflow DiscoGAN Implementation
# Licensed under The MIT License [see LICENSE for details]
# Written by Cheng-Bin Jin, based on code from vanhuyz
# Email: sbkim0407@gmail.com
# ---------------------------------------------------------
import collections
import numpy as np
# import matplotlib as mpl
import tensorflow as tf
# mpl.use('TkAgg')  # or whatever other backend that you want to solve Segmentation fault (core dumped)
# import matplotlib.pyplot as plt
# import matplotlib.gridspec as gridspec

import tensorflow_utils as tf_utils
import utils as utils
from reader import Reader


# noinspection PyPep8Naming
class DiscoGAN(object):
    def __init__(self, sess, flags, image_size, ori_image_size, data_path):
        self.sess = sess
        self.flags = flags
        self.image_size = image_size
        self.ori_image_size = ori_image_size
        self.x_path, self.y_path = data_path[0], data_path[1]

        self.norm = 'batch'
        self.lambda1, self.lambda2 = 1.0, 1.0
        self.ngf, self.ndf = 64, 64
        self.eps = 1e-12
        self.start_decay_step = int(np.ceil(self.flags.iters / 2))  # for optimizer
        self.decay_steps = self.flags.iters - self.start_decay_step

        self._G_gen_train_ops, self._F_gen_train_ops = [], []
        self._Dy_dis_train_ops, self._Dx_dis_train_ops = [], []

        self._build_net()
        self._tensorboard()
        self._cal_grid_size()

    def _build_net(self):
        if (self.flags.dataset == 'edges2handbags') or (self.flags.dataset == 'edges2shoes'):
            side_1, side_2 = 'left', 'right'
            self.input_channel = 1
            self.output_channel = 3
        elif self.flags.dataset == 'handbags2shoes':
            side_1, side_2 = 'right', 'right'
            self.input_channel = 3
            self.output_channel = 3
        else:
            side_1, side_2 = 'left', 'right'
            self.input_channel = 3
            self.output_channel = 3

        # tfph: tensorflow placeholder
        self.x_test_tfph = tf.placeholder(
            tf.float32, shape=[None, self.image_size[0], self.image_size[1], self.input_channel], name='A_test_tfph')
        self.y_test_tfph = tf.placeholder(
            tf.float32, shape=[None, self.image_size[0], self.image_size[1], self.output_channel], name='B_test_tfph')

        self.G_gen = Generator(name='G', ngf=self.ngf, norm=self.norm, output_channel=self.output_channel,
                               _ops=self._G_gen_train_ops)
        self.Dy_dis = Discriminator(name='Dy', ndf=self.ndf, norm=self.norm, _ops=self._Dy_dis_train_ops)
        self.F_gen = Generator(name='F', ngf=self.ngf, norm=self.norm, output_channel=self.input_channel,
                               _ops=self._F_gen_train_ops)
        self.Dx_dis = Discriminator(name='Dx', ndf=self.ndf, norm=self.norm, _ops=self._Dx_dis_train_ops)

        x_reader = Reader(self.x_path, name='X', image_size=self.image_size, batch_size=self.flags.batch_size,
                          side=side_1, ori_image_size=self.ori_image_size)
        y_reader = Reader(self.y_path, name='Y', image_size=self.image_size, batch_size=self.flags.batch_size,
                          side=side_2, ori_image_size=self.ori_image_size)

        if self.input_channel == 1:
            imgs = x_reader.feed()
            _, self.x_imgs, _ = tf.split(imgs, [1, 1, 1], axis=3)
        else:
            self.x_imgs = x_reader.feed()
        self.y_imgs = y_reader.feed()

        # cycle consistency loss
        self.cycle_loss = self.cycle_consistency_loss(self.x_imgs, self.y_imgs)

        # X -> Y
        self.fake_y_imgs = self.G_gen(self.x_imgs)
        self.G_gen_loss = self.generator_loss(self.Dy_dis, self.fake_y_imgs)
        self.G_reg = self.flags.weight_decay * tf.reduce_sum(
            [tf.nn.l2_loss(weight) for weight in tf.get_collection(key=tf.GraphKeys.TRAINABLE_VARIABLES, scope='G')])
        self.G_loss = self.G_gen_loss + self.cycle_loss + self.G_reg

        self.Dy_dis_loss = self.discriminator_loss(self.Dy_dis, self.y_imgs, self.fake_y_imgs)
        self.Dy_dis_reg = self.flags.weight_decay * tf.reduce_sum(
            [tf.nn.l2_loss(weight) for weight in tf.get_collection(key=tf.GraphKeys.TRAINABLE_VARIABLES, scope='Dy')])
        self.Dy_loss = self.Dy_dis_loss + self.Dy_dis_reg

        # Y -> X
        self.fake_x_imgs = self.F_gen(self.y_imgs)
        self.F_gen_loss = self.generator_loss(self.Dx_dis, self.fake_x_imgs)
        self.F_reg = self.flags.weight_decay * tf.reduce_sum(
            [tf.nn.l2_loss(weight) for weight in tf.get_collection(key=tf.GraphKeys.TRAINABLE_VARIABLES, scope='F')])
        self.F_loss = self.F_gen_loss + self.cycle_loss + self.F_reg

        self.Dx_dis_loss = self.discriminator_loss(self.Dx_dis, self.x_imgs, self.fake_x_imgs)
        self.Dx_dis_reg = self.flags.weight_decay * tf.reduce_sum(
            [tf.nn.l2_loss(weight) for weight in tf.get_collection(key=tf.GraphKeys.TRAINABLE_VARIABLES, scope='Dx')])
        self.Dx_loss = self.Dx_dis_loss + self.Dx_dis_reg

        # G_optim = tf.train.AdamOptimizer(
        #     learning_rate=self.flags.learning_rate, beta1=self.flags.beta1, beta2=self.flags.beta2).minimize(
        #     self.G_loss, var_list=self.G_gen.variables, name='Adam_G')
        # Dy_optim = tf.train.AdamOptimizer(
        #     learning_rate=self.flags.learning_rate, beta1=self.flags.beta1, beta2=self.flags.beta2).minimize(
        #     self.Dy_loss, var_list=self.Dy_dis.variables, name='Adam_Dy')
        # F_optim = tf.train.AdamOptimizer(
        #     learning_rate=self.flags.learning_rate, beta1=self.flags.beta1, beta2=self.flags.beta2).minimize(
        #     self.F_loss, var_list=self.F_gen.variables, name='Adam_F')
        # Dx_optim = tf.train.AdamOptimizer(
        #     learning_rate=self.flags.learning_rate, beta1=self.flags.beta1, beta2=self.flags.beta2).minimize(
        #     self.Dx_loss, var_list=self.Dx_dis.variables, name='Adam_Dx')
        G_optim = self.optimizer(loss=self.G_loss, variables=self.G_gen.variables, name='Adam_G')
        Dy_optim = self.optimizer(loss=self.Dy_dis_loss, variables=self.Dy_dis.variables, name='Adam_Dy')
        F_optim = self.optimizer(loss=self.F_loss, variables=self.F_gen.variables, name='Adam_F')
        Dx_optim = self.optimizer(loss=self.Dx_dis_loss, variables=self.Dx_dis.variables, name='Adam_Dx')
        self.optims = tf.group([G_optim, Dy_optim, F_optim, Dx_optim])

        # for sampling function
        self.fake_y_sample = self.G_gen(self.x_test_tfph)
        self.fake_x_sample = self.F_gen(self.y_test_tfph)

    def optimizer(self, loss, variables, name='Adam'):
        global_step = tf.Variable(0, trainable=False)
        starter_learning_rate = self.flags.learning_rate
        end_learning_rate = 0.
        start_decay_step = self.start_decay_step
        decay_steps = self.decay_steps

        learning_rate = (tf.where(tf.greater_equal(global_step, start_decay_step),
                                  tf.train.polynomial_decay(starter_learning_rate,
                                                            global_step - start_decay_step,
                                                            decay_steps, end_learning_rate, power=1.0),
                                  starter_learning_rate))
        tf.summary.scalar('learning_rate/{}'.format(name), learning_rate)

        learn_step = tf.train.AdamOptimizer(learning_rate, beta1=self.flags.beta1, beta2=self.flags.beta2).\
            minimize(loss, global_step=global_step, var_list=variables, name=name)

        return learn_step

    def cycle_consistency_loss(self, x_imgs, y_imgs):
        # use mean squared error
        forward_loss = tf.reduce_mean(tf.losses.mean_squared_error(labels=x_imgs,
                                                                   predictions=self.F_gen(self.G_gen(x_imgs))))
        backward_loss = tf.reduce_mean(tf.losses.mean_squared_error(labels=y_imgs,
                                                                    predictions=self.G_gen(self.F_gen(y_imgs))))
        loss = self.lambda1 * forward_loss + self.lambda2 * backward_loss
        return loss

    @staticmethod
    def generator_loss(dis_obj, fake_img):
        # loss = -tf.reduce_mean(tf.log(dis_obj(fake_img) + self.eps))
        _, d_logit_fake = dis_obj(fake_img)
        loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=d_logit_fake,
                                                                      labels=tf.ones_like(d_logit_fake)))
        return loss

    @staticmethod
    def discriminator_loss(dis_obj, real_img, fake_img):
        # error_real = -tf.reduce_mean(tf.log(dis_obj(real_img) + self.eps))
        # error_fake = -tf.reduce_mean(tf.log(1. - dis_obj(fake_img) + self.eps))
        # loss = 0.5 * (error_real + error_fake)

        _, d_logit_real = dis_obj(real_img)
        _, d_logit_fake = dis_obj(fake_img)
        d_loss_real = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=d_logit_real,
                                                                             labels=tf.ones_like(d_logit_real)))
        d_loss_fake = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=d_logit_fake,
                                                                             labels=tf.zeros_like(d_logit_fake)))
        loss = d_loss_real + d_loss_fake

        return loss

    def _tensorboard(self):
        tf.summary.scalar('loss/G_loss', self.G_loss)
        tf.summary.scalar('loss/G_gen_loss', self.G_gen_loss)
        tf.summary.scalar('loss/G_reg', self.G_reg)
        tf.summary.scalar('loss/F_loss', self.F_loss)
        tf.summary.scalar('loss/F_gen_loss', self.F_gen_loss)
        tf.summary.scalar('loss/F_reg', self.F_reg)
        tf.summary.scalar('loss/cycle_loss', self.cycle_loss)
        tf.summary.scalar('loss/Dy_loss', self.Dy_loss)
        tf.summary.scalar('loss/Dy_dis_loss', self.Dy_dis_loss)
        tf.summary.scalar('loss/Dy_dis_reg', self.Dy_dis_reg)
        tf.summary.scalar('loss/Dx_loss', self.Dx_loss)
        tf.summary.scalar('loss/Dx_dis_loss', self.Dx_dis_loss)
        tf.summary.scalar('loss/Dx_dis_reg', self.Dx_dis_reg)
        self.summary_op = tf.summary.merge_all()

    def train_step(self):
        ops = [self.optims, self.G_loss, self.F_loss, self.Dy_loss, self.Dx_loss, self.summary_op, self.G_gen_loss,
               self.G_reg, self.F_gen_loss, self.F_reg, self.cycle_loss, self.Dy_dis_loss, self.Dy_dis_reg,
               self.Dx_dis_loss, self.Dx_dis_reg]
        # ops_1 = [self.G_gen_loss, self.G_reg, self.F_gen_loss, self.F_reg, self.cycle_loss, self.Dy_dis_loss,
        #          self.Dy_dis_reg, self.Dx_dis_loss, self.Dx_dis_reg]

        _, G_loss, F_loss, Dy_loss, Dx_loss, summary, G_gen_loss, G_reg, F_gen_loss, F_reg, cycle_loss, Dy_dis_loss, \
        Dy_dis_reg, Dx_dis_loss, Dx_dis_reg = self.sess.run(ops)
        # G_gen_loss, G_reg, F_gen_loss, F_reg, cycle_loss, Dy_dis_loss, Dy_dis_reg, Dx_dis_loss, Dx_dis_reg = \
        #     self.sess.run(ops_1)

        return [G_loss, G_gen_loss, G_reg, F_loss, F_gen_loss, F_reg, cycle_loss, Dy_loss, Dy_dis_loss, Dy_dis_reg,
                Dx_loss, Dx_dis_loss, Dx_dis_reg], summary

    def sample_imgs(self):
        x_val, y_val = self.sess.run([self.x_imgs, self.y_imgs])
        fake_y, fake_x = self.sess.run([self.fake_y_sample, self.fake_x_sample],
                                       feed_dict={self.x_test_tfph: x_val, self.y_test_tfph: y_val})
        fake_yxy, fake_xyx = self.sess.run([self.fake_y_sample, self.fake_x_sample],
                                           feed_dict={self.x_test_tfph: fake_x, self.y_test_tfph: fake_y})

        names = ['A', 'AB', 'B', 'BA', 'ABA', 'BAB']
        return [x_val, fake_y, y_val, fake_x, fake_xyx, fake_yxy], names

    def test_step(self, x_img, y_img):
        if self.input_channel == 1:
            x_img = x_img[:, :, :, 1:2]
        else:
            x_img = x_img

        fake_y, fake_x = self.sess.run([self.fake_y_sample, self.fake_x_sample],
                                       feed_dict={self.x_test_tfph: x_img, self.y_test_tfph: y_img})
        fake_yxy, fake_xyx = self.sess.run([self.fake_y_sample, self.fake_x_sample],
                                           feed_dict={self.x_test_tfph: fake_x, self.y_test_tfph: fake_y})

        names = ['A', 'AB', 'B', 'BA', 'ABA', 'BAB']
        return [x_img, fake_y, y_img, fake_x, fake_xyx, fake_yxy], names

    def test_infinitely(self, input_type, count=5):
        x_val, y_val = self.sess.run([self.x_imgs, self.y_imgs])

        if input_type.upper() == 'A':
            iterator = [self.G_gen(self.x_test_tfph), self.F_gen(self.y_test_tfph)] * count
            input_img = x_val
            add_name = ['B', 'A']
            place_holder = [self.x_test_tfph, self.y_test_tfph]
        elif input_type.upper() == 'B':
            iterator = [self.F_gen(self.y_test_tfph), self.G_gen(self.x_test_tfph)] * count
            input_img = y_val
            add_name = ['A', 'B']
            place_holder = [self.y_test_tfph, self.x_test_tfph]

        else:
            raise NotImplementedError

        results = [input_img]
        names = [input_type]
        for step, model in enumerate(iterator):
            names.append(names[-1] + add_name[np.mod(step, 2)])
            output_img = self.sess.run(model, feed_dict={place_holder[np.mod(step, 2)]: input_img})
            results.append(output_img)
            input_img = output_img

        return results, names

    def print_info(self, loss, iter_time):
        if np.mod(iter_time, self.flags.print_freq) == 0:
            ord_output = collections.OrderedDict([('cur_iter', iter_time), ('tar_iters', self.flags.iters),
                                                  ('batch_size', self.flags.batch_size),
                                                  ('G_loss', loss[0]), ('G_gen_loss', loss[1]),
                                                  ('G_reg', loss[2]), ('F_loss', loss[3]),
                                                  ('F_gen_loss', loss[4]), ('F_reg', loss[5]),
                                                  ('cycle_loss', loss[6]), ('Dy_loss', loss[7]),
                                                  ('Dy_dis_loss', loss[8]), ('Dy_dis_reg', loss[9]),
                                                  ('Dx_loss', loss[10]), ('Dx_dis_loss', loss[11]),
                                                  ('Dx_dis_reg', loss[12]), ('dataset', self.flags.dataset),
                                                  ('gpu_index', self.flags.gpu_index)])

            utils.print_metrics(iter_time, ord_output)

    def plots(self, imgs, iter_time, save_file, names=None):
        canvas = len(imgs)

        # transform [-1., 1.] to [0., 1.]
        imgs = [utils.inverse_transform(imgs[idx]) for idx in range(len(imgs))]

        # save more bigger image
        for canvas_idx in range(canvas):
            utils.plots(imgs[canvas_idx], iter_time, save_file, self.grid_cols, self.grid_rows,
                        self.flags.sample_batch, name=names[canvas_idx])

    def _cal_grid_size(self, ruler=16):
        while np.mod(self.flags.sample_batch, ruler) != 0:
            ruler /= 2

        self.grid_cols, self.grid_rows = int(ruler), int(self.flags.sample_batch / ruler)


class Generator(object):
    def __init__(self, name=None, ngf=64, norm='instance', output_channel=3, _ops=None):
        self.name = name
        self.ngf = ngf
        self.output_channel = output_channel
        self.conv_dims = [self.ngf, 2*self.ngf, 4*self.ngf, 8*self.ngf]
        self.deconv_dims = [4*self.ngf, 2*self.ngf, self.ngf]
        self.norm = norm
        self._ops = _ops
        self.reuse = False

    def __call__(self, x):
        with tf.variable_scope(self.name, reuse=self.reuse):
            tf_utils.print_activations(x)

            # conv: (N, H, W, C) -> (N, H/2, W/2, 64)
            output = tf_utils.conv2d(x, self.conv_dims[0], k_h=4, k_w=4, d_h=2, d_w=2, padding='SAME',
                                     name='conv0_conv2d')
            output = tf_utils.lrelu(output, name='conv0_lrelu', is_print=True)

            for idx, conv_dim in enumerate(self.conv_dims[1:]):
                # conv: (N, H/2, W/2, C) -> (N, H/4, W/4, 2C)
                output = tf_utils.conv2d(output, conv_dim, k_h=4, k_w=4, d_h=2, d_w=2, padding='SAME',
                                         name='conv{}_conv2d'.format(idx+1))
                output = tf_utils.norm(output, _type=self.norm, _ops=self._ops, name='conv{}_norm'.format(idx+1))
                output = tf_utils.lrelu(output, name='conv{}_lrelu'.format(idx+1), is_print=True)

            for idx, deconv_dim in enumerate(self.deconv_dims):
                # deconv: (N, H/16, W/16, C) -> (N, W/8, H/8, C/2)
                output = tf_utils.deconv2d(output, deconv_dim, k_h=4, k_w=4, name='deconv{}_conv2d'.format(idx))
                output = tf_utils.norm(output, _type=self.norm, _ops=self._ops, name='deconv{}_norm'.format(idx))
                output = tf_utils.relu(output, name='deconv{}_relu'.format(idx), is_print=True)

            # conv: (N, H/2, W/2, 64) -> (N, W, H, 3)
            output = tf_utils.deconv2d(output, self.output_channel, k_h=4, k_w=4, name='conv3_deconv2d')
            output = tf_utils.tanh(output, name='conv4_tanh', is_print=True)

            # set reuse=True for next call
            self.reuse = True
            self.variables = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name)
            return output


class Discriminator(object):
    def __init__(self, name=None, ndf=64, norm='instance', _ops=None):
        self.name = name
        self.ndf = ndf
        self.hidden_dims = [self.ndf, 2*self.ndf, 4*self.ndf, 8*self.ndf]
        self.norm = norm
        self._ops = _ops
        self.reuse = False

    def __call__(self, x):
        with tf.variable_scope(self.name, reuse=self.reuse):
            tf_utils.print_activations(x)

            # conv: (N, H, W, 3) -> (N, H/2, W/2, 64)
            output = tf_utils.conv2d(x, self.ndf, k_h=4, k_w=4, d_h=2, d_w=2, padding='SAME', name='conv0_conv2d')
            output = tf_utils.lrelu(output, name='conv0_lrelu', is_print=True)

            for idx, hidden_dim in enumerate(self.hidden_dims[1:]):
                # conv: (N, H/2, W/2, C) -> (N, H/4, W/4, C/2)
                output = tf_utils.conv2d(output, hidden_dim, k_h=4, k_w=4, d_h=2, d_w=2, padding='SAME',
                                         name='conv{}_conv2d'.format(idx+1))
                output = tf_utils.norm(output, _type=self.norm, _ops=self._ops, name='conv{}_norm'.format(idx+1))
                output = tf_utils.lrelu(output, name='conv{}_lrelu'.format(idx+1), is_print=True)

            # conv: (N, H/16, W/16, 512) -> (N, H/16, W/16, 1)
            output = tf_utils.conv2d(output, 1, k_h=4, k_w=4, d_h=1, d_w=1, padding='SAME', name='conv4_conv2d')

            # set reuse=True for next call
            self.reuse = True
            self.variables = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name)

            return tf_utils.sigmoid(output), output
