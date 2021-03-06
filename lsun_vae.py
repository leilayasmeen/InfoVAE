import matplotlib
matplotlib.use('Agg')
import tensorflow as tf
import numpy as np
import os, time
import subprocess
import argparse
from scipy import misc as misc
from logger import *
from abstract_network import *
from dataset import *

parser = argparse.ArgumentParser()
# python coco_transfer2.py --db_path=../data/coco/coco_seg_transfer40_30_299 --batch_size=64 --gpu='0' --type=mask

parser.add_argument('-g', '--gpu', type=str, default='1', help='GPU to use')
parser.add_argument('-z', '--zdim', type=int, default=100, help='Dimensionality of z')
parser.add_argument('-y', '--ydim', type=int, default=100, help='Dimensionality of y')
parser.add_argument('-n', '--name', type=str, default='', help='Run name')
parser.add_argument('-d', '--db_path', type=str, default='../data/bedroom', help='LSUN path')
args = parser.parse_args()


# python mmd_vae_eval.py --reg_type=elbo --gpu=0 --train_size=1000
os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
batch_size = 100


def make_model_path(name):
    log_path = os.path.join('log', name)
    if os.path.isdir(log_path):
        subprocess.call(('rm -rf %s' % log_path).split())
    os.makedirs(log_path)
    return log_path

if len(args.name) == 0:
    args.name = 'lsun_vae_e%d_%d' % (args.zdim, args.ydim)
log_path = make_model_path(args.name)


# Encoder and decoder use the DC-GAN architecture
# 28 x 28 x 1
def encoder(x, z_dim):
    with tf.variable_scope('encoder'):
        conv = conv2d_bn_lrelu(x, 48, 4, 2)
        conv = conv2d_bn_lrelu(conv, 48, 4, 1)
        conv = conv2d_bn_lrelu(conv, 96, 4, 2)
        conv = conv2d_bn_lrelu(conv, 96, 4, 1)
        conv = conv2d_bn_lrelu(conv, 192, 4, 2)
        conv = conv2d_bn_lrelu(conv, 192, 4, 1)
        conv = conv2d_bn_lrelu(conv, 512, 4, 2)
        conv = conv2d_bn_lrelu(conv, 512, 4, 1)  # None x 4 x 4 x 256
        conv = tf.reshape(conv, [-1, np.prod(conv.get_shape().as_list()[1:])])
        fc = fc_lrelu(conv, 2048)
        mean = tf.contrib.layers.fully_connected(fc, z_dim, activation_fn=tf.identity)
        stddev = tf.contrib.layers.fully_connected(fc, z_dim, activation_fn=tf.sigmoid)
        stddev = tf.maximum(stddev, 0.01)
        mean = tf.maximum(tf.minimum(mean, 10.0), -10.0)
        return mean, stddev


def decoder(z, reuse=False):
    with tf.variable_scope('decoder') as vs:
        if reuse:
            vs.reuse_variables()
        fc = fc_relu(z, 2048)
        fc = fc_relu(fc, 4*4*512)
        fc = tf.reshape(fc, tf.stack([tf.shape(fc)[0], 4, 4, 512]))
        conv = conv2d_t_bn_lrelu(fc, 512, 4, 1)
        conv = conv2d_t_bn_lrelu(conv, 192, 4, 2)
        conv = conv2d_t_bn_lrelu(conv, 192, 4, 1)
        conv = conv2d_t_bn_lrelu(conv, 96, 4, 2)
        conv = conv2d_t_bn_lrelu(conv, 96, 4, 1)
        conv = conv2d_t_bn_lrelu(conv, 48, 4, 2)
        conv = conv2d_t_bn_lrelu(conv, 48, 4, 1)
        mean = tf.contrib.layers.convolution2d_transpose(conv, 3, 4, 2, activation_fn=tf.sigmoid)
        mean = mean * 2.0 - 1.0
        return mean


def encoder2(z, y_dim):
    with tf.variable_scope('encoder2') as vs:
        fc = fc_lrelu(z, 2048)
        fc = fc_lrelu(fc, 2048)
        mean = tf.contrib.layers.fully_connected(fc, y_dim, activation_fn=tf.identity)
        mean = tf.maximum(tf.minimum(mean, 10.0), -10.0)
        stddev = tf.contrib.layers.fully_connected(fc, y_dim, activation_fn=tf.sigmoid)
        stddev = tf.maximum(stddev, 0.01)
        return mean, stddev


def decoder2(y, z_dim, reuse=False):
    with tf.variable_scope('decoder2') as vs:
        if reuse:
            vs.reuse_variables()
        fc = fc_relu(y, 2048)
        fc = fc_relu(fc, 2048)
        mean = tf.contrib.layers.fully_connected(fc, z_dim, activation_fn=tf.identity)
        mean = tf.maximum(tf.minimum(mean, 10.0), -10.0)
        stddev = tf.contrib.layers.fully_connected(fc, z_dim, activation_fn=tf.sigmoid)
        stddev = tf.maximum(stddev, 0.01)
        return mean, stddev


# Build the computation graph for training
z_dim = args.zdim
y_dim = args.ydim
x_dim = [64, 64, 3]
train_x = tf.placeholder(tf.float32, shape=[None] + x_dim)
train_zmean, train_zstddev = encoder(train_x, z_dim)
train_z = train_zmean + tf.multiply(train_zstddev,
                                    tf.random_normal(tf.stack([tf.shape(train_x)[0], z_dim])))
train_xr = decoder(train_z)

train_ymean, train_ystddev = encoder2(train_z, y_dim)
train_y = train_ymean + tf.multiply(train_ystddev,
                                    tf.random_normal(tf.stack([tf.shape(train_x)[0], y_dim])))
train_zrmean, train_zrstddev = decoder2(train_y, z_dim)


def compute_kl(mean1, stddev1, mean2, stddev2):
    return tf.log(stddev2) - tf.log(stddev1) + \
           (tf.square(stddev1) + tf.square(mean1 - mean2)) / tf.square(stddev2) / 2 - 0.5

# Build the computation graph for generating samples
gen_z = tf.placeholder(tf.float32, shape=[None, z_dim])
gen_x = decoder(gen_z, reuse=True)

gen2_y = tf.placeholder(tf.float32, shape=[None, y_dim])
gen2_zmean, gen2_zstddev = decoder2(gen2_y, z_dim, reuse=True)
gen2_z = gen2_zmean + tf.multiply(gen2_zstddev,
                                  tf.random_normal(tf.stack([tf.shape(train_x)[0], z_dim])))
gen2_x = decoder(gen2_z, reuse=True)

# ELBO loss divided by input dimensions
loss_elbo_per_sample = tf.reduce_mean(-tf.log(train_zstddev) + 0.5 * tf.square(train_zstddev) +
                                     0.5 * tf.square(train_zmean) - 0.5, axis=1)
loss_elbo = tf.reduce_mean(loss_elbo_per_sample)

loss_elbo2_per_sample = tf.reduce_mean(-tf.log(train_ystddev) + 0.5 * tf.square(train_ystddev) +
                                     0.5 * tf.square(train_ymean) - 0.5, axis=1)
loss_elbo2 = tf.reduce_mean(loss_elbo2_per_sample)

# Negative log likelihood per dimension
loss_nll = 30.0 * tf.reduce_mean(tf.reduce_mean(tf.abs(train_xr - train_x), axis=(1, 2, 3)))
loss_nll2 = 10 * tf.reduce_mean(compute_kl(train_zmean, train_zstddev, train_zrmean, train_zrstddev))

reg_coeff = tf.placeholder(tf.float32, shape=[])
loss_all = loss_nll + loss_nll2 + reg_coeff * (loss_elbo + loss_elbo2)

trainer = tf.train.AdamOptimizer(1e-4).minimize(loss_all)
train_summary = tf.summary.merge([
    tf.summary.scalar('elbo', loss_elbo),
    tf.summary.scalar('elbo2', loss_elbo2),
    tf.summary.scalar('reconstruction', loss_nll),
    tf.summary.scalar('reconstruction2', loss_nll2),
    tf.summary.scalar('loss', loss_all)
])

img_summary = tf.summary.merge([
    create_multi_display([tf.reshape(train_x, [batch_size, 64, 64, 3]),
                          tf.reshape(train_xr, [batch_size, 64, 64, 3])], 'train'),
    create_display(tf.reshape(gen_x, [batch_size, 64, 64, 3]), 'samples'),
    create_display(tf.reshape(gen2_x, [batch_size, 64, 64, 3]), 'samples2')
])
dataset = LSUNDataset(db_path=args.db_path)

gpu_options = tf.GPUOptions(allow_growth=True)
sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options, allow_soft_placement=True))
sess.run(tf.global_variables_initializer())
summary_writer = tf.summary.FileWriter(log_path)

# Start training
# plt.ion()
for i in range(100000):
    batch_x = dataset.next_batch(batch_size)
    if i < 20000:
        reg_val = 0.01
    else:
        reg_val = 1.0
    _, loss, nll, elbo = \
        sess.run([trainer, loss_all, loss_nll, loss_elbo], feed_dict={train_x: batch_x, reg_coeff: reg_val})
    if i % 100 == 0:
        print("Iteration %d, nll %.4f, elbo loss %.4f" % (i, nll, elbo))
        summary_writer.add_summary(sess.run(train_summary, feed_dict={train_x: batch_x, reg_coeff: reg_val}), i)
    if i % 2000 == 0:
        bz = np.random.normal(size=(batch_size, z_dim))
        by = np.random.normal(size=(batch_size, y_dim))
        summary_writer.add_summary(sess.run(img_summary, feed_dict={train_x: batch_x, reg_coeff: reg_val, gen_z: bz, gen2_y: by}), i)
        # if i % 2000 == 0:
        #     samples_mean = sess.run(gen_x, feed_dict={gen_z: bz})
        #     plots = convert_to_display(samples_mean)
        #     misc.imsave(os.path.join(log_path, 'samples%d.png' % i), plots)


