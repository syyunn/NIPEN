from utils import evaluation,evaluation_not_voting,make_records,make_records_original,SDAE_calculate,VAE_calculate,l2_norm,variable_save
from models.contents_network_scale_model import base_model,user_base_model,doc_base_model,user_doc_base_model,without_network_model,only_network_model
import tensorflow as tf
import numpy as np
import matplotlib
matplotlib.use('Agg')
import time


class NIPEN():
    def __init__(self, sess,args,model_name,display_step,current_time,data_name,test_fold,random_seed,
                 num_doc, num_voca, num_user, num_topic, num_train_voting, num_test_voting, pre_W, pre_b,\
                 batch_size, nipen_epoch, nipen_learning_rate, optimizer, nipen_decay_rate,\
                 f_act,g_act, encoder_method, do_batch_norm, keep_prob,nipen_corruption_level,network_structure,layer_structure, \
                 bias_structure,use_bias_reg,
                 X_dw, train_v_ud, test_v_ud, mask_train_v_ud, mask_test_v_ud, trust_matrix, lambda_list,init_constant_alpha,result_record_directory,use_network_positive,
                 network_split,G):

        self.sess = sess
        self.args = args
        self.model_name = model_name
        self.current_time = current_time
        self.data_name = data_name
        self.test_fold = test_fold
        self.random_seed = random_seed

        self.num_doc = num_doc
        self.num_voca = num_voca
        self.num_user = num_user  # num user
        self.num_topic = num_topic  # num topic
        self.num_train_voting = num_train_voting
        self.num_test_voting = num_test_voting
        self.W = pre_W  # SDAE parameter (Weight)
        self.b = pre_b  # SDAE parameter (bias)

        self.batch_size = batch_size
        self.nipen_epoch = nipen_epoch
        self.nipen_learning_rate = nipen_learning_rate
        self.nipen_decay_rate = nipen_decay_rate
        self.optimizer_method = optimizer

        self.f_act = f_act
        self.g_act = g_act
        self.encoder_method = encoder_method
        self.do_batch_norm = do_batch_norm
        self.train_keep_prob = keep_prob
        self.test_keep_prob = 1
        self.train_nipen_corruption_level = nipen_corruption_level
        self.test_nipen_corruption_level = 0
        self.network_structure = network_structure
        self.layer_structure = layer_structure
        self.bias_structure = bias_structure
        self.use_bias_reg = use_bias_reg
        self.use_network_positive = use_network_positive

        self.lambda_y = lambda_list[0]  # xi_dk prior std / cost3
        self.lambda_u = lambda_list[1]  # x_uk prior std / cost5
        self.lambda_w = lambda_list[2]  # SDAE weight std. / weight , bias regularization / cost1
        self.lambda_n = lambda_list[3]  # SDAE output (cost2)
        self.lambda_tau = lambda_list[4]  # tau_uu prior std / cost6
        self.lambda_f = lambda_list[5]  # voting result prior std / cost4
        self.lambda_alpha = lambda_list[6]  # to regularize alpha matrix
        self.lambda_not_voting = lambda_list[7]  # to make alpha positive
        self.lambda_penalty = lambda_list[8]  # to make alpha positive

        self.X_dw = X_dw
        self.train_v_ud = train_v_ud
        self.test_v_ud = test_v_ud
        self.mask_train_v_ud = mask_train_v_ud
        self.mask_test_v_ud = mask_test_v_ud
        self.trust_matrix = trust_matrix
        self.lambda_list = lambda_list
        self.init_constant_alpha = init_constant_alpha

        self.display_step = display_step
        self.Train_Acc = []
        self.Train_RMSE = []
        self.Train_Avg_log_likelihood = []
        self.Train_cost = []

        self.test_overall_acc_list = []
        self.test_aye_acc_list = []
        self.test_nay_acc_list = []
        self.test_notvoting_acc_list = []

        self.test_overall_RMSE_list = []
        self.test_aye_RMSE_list = []
        self.test_nay_RMSE_list = []
        self.test_notvoting_RMSE_list = []

        self.test_overall_MAE_list = []
        self.test_aye_MAE_list = []
        self.test_nay_MAE_list = []
        self.test_notvoting_MAE_list = []

        self.result_path = result_record_directory

        self.train_var_list1 = [] # U , V
        self.train_var_list2 = [] # W , b
        self.step = tf.Variable(0, trainable=False)

        self.earlystop_switch = False
        self.min_RMSE = 99999
        self.min_epoch = -99999
        self.patience = 0
        self.total_patience = 20

        self.network_split = network_split
        self.G = G

        self.num_aye_ratings = np.sum((self.test_v_ud == 1) & (self.mask_test_v_ud == 1))
        self.num_nay_ratings = np.sum((self.test_v_ud == -1) & (self.mask_test_v_ud == 1))
        self.num_notvoting_ratings = np.sum((self.test_v_ud == 0) & (self.mask_test_v_ud == 1))

        self.test_rmse_list = []
        self.test_mae_list = []
        self.test_acc_list = []
        self.test_avg_loglike_list = []
        self.test_cost_list = []

    def run(self):
        self.prepare_model()
        init = tf.global_variables_initializer()
        self.sess.run(init)
        for epoch_itr in range(self.nipen_epoch):
            if self.earlystop_switch:
                break
            else:
                self.train(epoch_itr)
                self.test(epoch_itr)
        make_records_original(self.result_path, self.test_acc_list, self.test_rmse_list, self.test_mae_list,
                     self.test_avg_loglike_list, self.current_time,
                     self.args, self.model_name, self.data_name, self.test_fold, self.num_topic, self.random_seed,
                     self.optimizer_method, self.nipen_learning_rate)

    def prepare_model(self):
        '''==================== placeholder initialization (input / corruption / dropout)  ===================='''
        self.model_mask_corruption = tf.placeholder(dtype=tf.float32, shape=[None, self.num_voca])
        self.model_X = tf.placeholder(dtype=tf.float32, shape=[None, self.num_voca], name='X')
        self.model_input_v_ud = tf.placeholder(dtype=tf.float32, shape=[self.num_user, None])
        self.model_mask_v_ud = tf.placeholder(dtype=tf.float32,
                                              shape=[self.num_user, None])  # to consider non-zero v_ud
        self.model_num_voting = tf.placeholder(dtype=tf.float32, )
        self.model_keep_prob = tf.placeholder(dtype=tf.float32)
        self.model_batch_data_idx = tf.placeholder(dtype=tf.int32)
        self.num_eps_samples = tf.placeholder(dtype=tf.int32)
        X_c = tf.multiply(self.model_mask_corruption, self.model_X)  ### Corrupted input
        # real_batch_size = tf.shape(self.model_X, out_type=tf.int32)[0]
        real_batch_size = tf.cast(tf.shape(self.model_X)[0], tf.int32)

        ''' ==================== SDAE / VAE Calculation (Encoder / Decoder) ==================== '''
        if self.encoder_method == "SDAE":
            Encoded_X, sdae_output = SDAE_calculate(self.model_name, X_c, self.layer_structure, self.W, self.b,
                                                    self.do_batch_norm, self.f_act, self.g_act, self.model_keep_prob)
        elif self.encoder_method == "VAE":
            z, vae_cost, l2_reg = VAE_calculate(self.sess, X_c, self.layer_structure, self.W, self.b,
                                                self.do_batch_norm,
                                                self.f_act, self.g_act, self.model_keep_prob, self.lambda_w,
                                                self.lambda_n, self.num_eps_samples)
            # vae_cost : recon error
            # l2_reg : regularizer
            #  z : Encoder

        ''' ==================== initialize the pre-variable  ==================== '''
        init_user_bias = np.zeros([1, self.num_user])
        init_doc_bias = np.zeros([1, self.num_doc])
        init_user_doc_bias = np.zeros([self.num_user, self.num_doc])
        self.v_ud = tf.convert_to_tensor(self.model_input_v_ud, dtype=tf.float32)
        with tf.variable_scope("Ideal_Variable"):
            self.y_dk = tf.get_variable(name="y_dk",
                                        initializer=tf.truncated_normal(shape=[self.num_doc, self.num_topic],
                                                                        mean=0, stddev=tf.truediv(1.0, self.lambda_y)),
                                        dtype=tf.float32)
            self.a_dk = tf.get_variable(name="a_dk",
                                        initializer=tf.truncated_normal(shape=[self.num_doc, self.num_topic],
                                                                        mean=0, stddev=tf.truediv(1.0, self.lambda_u)),
                                        dtype=tf.float32)
            self.x_uk = tf.get_variable(name="x_uk",
                                        initializer=tf.truncated_normal(shape=[self.num_user, self.num_topic],
                                                                        mean=0, stddev=tf.truediv(1.0, self.lambda_u)),
                                        dtype=tf.float32)

        ''' ==================== initialize the variable (with batch_size) ==================== '''
        batch_y_dk = tf.reshape(tf.gather(self.y_dk, self.model_batch_data_idx), [real_batch_size, self.num_topic])
        batch_a_dk = tf.reshape(tf.gather(self.a_dk, self.model_batch_data_idx), [real_batch_size, self.num_topic])

        if self.network_structure == 'without_network':
            self.tau_uu = tf.constant(0, dtype=tf.float32)
        elif self.network_split == 'True':
            tmp_A_mat = tf.get_variable(name="tmp_A_mat", initializer=tf.truncated_normal(shape=[self.num_user, self.G],
                                                                                          mean=0, stddev=tf.truediv(1.0,
                                                                                                                    self.lambda_tau)),
                                        dtype=tf.float32)
            tmp_B_mat = tf.get_variable(name="tmp_B_mat", initializer=tf.truncated_normal(shape=[self.G, self.num_user],
                                                                                          mean=0, stddev=tf.truediv(1.0,
                                                                                                                    self.lambda_tau)),
                                        dtype=tf.float32)
            self.tau_uu = tf.matmul(tmp_A_mat, tmp_B_mat)
        else:
            with tf.variable_scope("Ideal_Variable"):
                self.tau_uu = tf.get_variable(name="tau_uu",
                                              initializer=tf.truncated_normal(shape=[self.num_user, self.num_user],
                                                                              mean=0,
                                                                              stddev=tf.truediv(1.0, self.lambda_tau)),
                                              dtype=tf.float32)
        if self.network_structure == "alpha":
            self.contents_scale_matrix, self.network_scale_matrix = base_model(self.num_doc, self.num_user,
                                                                               real_batch_size,
                                                                               self.model_batch_data_idx)
        elif self.network_structure == "user_alpha":
            self.contents_scale_matrix, self.network_scale_matrix, self.user_contents_alpha, self.user_network_alpha = user_base_model(
                self.num_doc, self.num_user, real_batch_size, self.model_batch_data_idx, self.lambda_alpha)
        elif self.network_structure == "doc_alpha":
            self.contents_scale_matrix, self.network_scale_matrix = doc_base_model(self.num_doc, self.num_user,
                                                                                   real_batch_size,
                                                                                   self.model_batch_data_idx)
        elif self.network_structure == "user_doc_alpha":
            self.contents_scale_matrix, self.network_scale_matrix = user_doc_base_model(self.num_doc, self.num_user,
                                                                                        real_batch_size,
                                                                                        self.model_batch_data_idx)
        elif self.network_structure == "without_network":
            self.contents_scale_matrix, self.network_scale_matrix = without_network_model(self.num_doc, self.num_user,
                                                                                          real_batch_size,
                                                                                          self.model_batch_data_idx)
        elif self.network_structure == "only_network":
            self.contents_scale_matrix, self.network_scale_matrix = only_network_model(self.num_doc, self.num_user,
                                                                                       real_batch_size,
                                                                                       self.model_batch_data_idx)
        ''' ==================== Cost Construction  ==================== '''
        if self.encoder_method == "SDAE":
            pre_cost1 = tf.constant(0, dtype=tf.float32)
            for itr in range(len((self.W).keys())):
                pre_cost1 = tf.add(pre_cost1,
                                   tf.add(tf.square(l2_norm(self.W[itr])), tf.square(l2_norm(self.b[itr]))))
            pre_cost2 = tf.square(l2_norm(sdae_output - self.model_X))
            pre_cost3 = tf.square(l2_norm(batch_y_dk - Encoded_X))

        if self.encoder_method == "VAE":
            pre_cost1 = l2_reg
            pre_cost2 = vae_cost
            pre_cost3 = tf.square(l2_norm(batch_y_dk - z))

        ''' make p_v_ud'''
        tf_mask_vud = tf.convert_to_tensor(self.model_mask_v_ud, dtype=tf.float32)
        mask_tau_uu = self.trust_matrix
        tf_mask_tau_uu = tf.convert_to_tensor(mask_tau_uu, dtype=tf.float32)
        network_part = tf.matmul(tf.multiply(self.tau_uu, tf_mask_tau_uu), self.v_ud)
        contents_part = tf.matmul(self.x_uk, tf.transpose(tf.multiply(batch_y_dk, batch_a_dk)))
        if self.bias_structure == "user":
            user_bias = tf.Variable(init_user_bias, dtype=tf.float32)
            user_bias_matrix = tf.transpose(tf.matmul(tf.ones(shape=[self.num_doc, 1]), user_bias))
            batch_user_bias_matrix = tf.reshape(
                tf.transpose(tf.gather(tf.transpose(user_bias_matrix), self.model_batch_data_idx)),
                [self.num_user, real_batch_size])
            contents_part = contents_part + batch_user_bias_matrix
            bias_regularizer = tf.pow(l2_norm(batch_user_bias_matrix), 2)
        elif self.bias_structure == "doc":
            with tf.variable_scope("Ideal_Variable"):
                doc_bias = tf.Variable(init_doc_bias, dtype=tf.float32)
            doc_bias_matrix = tf.matmul(tf.ones(shape=[self.num_user, 1]), doc_bias)
            batch_doc_bias_matrix = tf.reshape(
                tf.transpose(tf.gather(tf.transpose(doc_bias_matrix), self.model_batch_data_idx)),
                [self.num_user, real_batch_size])
            contents_part = contents_part + batch_doc_bias_matrix
            bias_regularizer = tf.pow(l2_norm(batch_doc_bias_matrix), 2)

        if self.use_network_positive == "True":
            pre_p_vud = tf.multiply(tf.multiply(self.network_scale_matrix, self.network_scale_matrix), network_part) \
                        + tf.multiply(tf.multiply(self.contents_scale_matrix, self.contents_scale_matrix),
                                      contents_part)
        elif self.use_network_positive == "False":
            pre_p_vud = tf.multiply(self.network_scale_matrix, network_part) \
                        + tf.multiply(self.contents_scale_matrix, contents_part)
        else:
            raise Exception('Network Structure is Strange')

        self.p_vud = tf.nn.sigmoid(pre_p_vud)
        # self.p_vud = pre_p_vud

        ''' Cost4'''

        pre_cost4 = (self.v_ud) * tf.log(self.p_vud + (1e-12)) + (1 - self.v_ud) * tf.log(1 - self.p_vud + (1e-12))
        pre_cost4_with_nonzero_vud = tf.multiply(pre_cost4, tf_mask_vud)
        pre_cost4 = tf.reduce_sum(pre_cost4_with_nonzero_vud)
        '''
        pre_pre_cost4 = tf.mul(tf.pow(l2_norm(self.p_vud - self.v_ud),2),tf_mask_vud)
        pre_cost4 = tf.reduce_sum(pre_pre_cost4)
        '''
        ''' Cost5'''
        pre_cost5 = self.lambda_u * tf.pow(l2_norm(batch_a_dk), 2) \
                    + self.lambda_u * tf.pow(l2_norm(self.x_uk), 2) \
                    + self.lambda_tau * tf.pow(l2_norm(self.tau_uu), 2) \
                    + self.lambda_alpha * tf.pow(l2_norm(self.contents_scale_matrix), 2) \
                    + self.lambda_alpha * tf.pow(l2_norm(self.network_scale_matrix), 2)
        if self.use_bias_reg:
            pre_cost5 = pre_cost5 + bias_regularizer

        # cost1 : positive / cost2 : positive / cost3 : positive / cost4 : negative / cost5 : positive
        self.cost1 = 0.5 * self.lambda_w * pre_cost1
        if self.model_name == 'NIPEN_with_VAE':
            self.cost2 = pre_cost2
        elif self.model_name == 'NIPEN':
            self.cost2 = 0.5 * self.lambda_n * pre_cost2
        else:
            raise Exception('Cost Model Name Error')

        self.cost3 = 0.5 * self.lambda_y * pre_cost3
        self.cost4 = -0.5 * self.lambda_f * pre_cost4  # negative
        self.cost5 = 0.5 * pre_cost5

        # self.cost6 = -1 * self.lambda_penalty * tf.reduce_sum(tf.minimum(self.contents_scale_matrix , 0) + tf.minimum(self.network_scale_matrix , 0))

        self.cost = self.cost1 + self.cost2 + self.cost3 + self.cost4 + self.cost5  # + self.cost6
        print("================= End of cost construction =================")

        for var in tf.trainable_variables():
            if ("Ideal_Variable" in var.name):
                self.train_var_list1.append(var)
            if self.encoder_method == "SDAE":
                if ("SDAE_Variable" in var.name):
                    self.train_var_list2.append(var)
            elif self.encoder_method == "VAE":
                if ("VAE_Variable" in var.name):
                    self.train_var_list2.append(var)
            else:
                raise Exception('Variable non-optimization Error')

        if self.optimizer_method == "Adam":
            optimizer1 = tf.train.AdamOptimizer(self.nipen_learning_rate)
            optimizer2 = tf.train.AdamOptimizer(self.nipen_learning_rate)
        elif self.optimizer_method == "Momentum":
            optimizer1 = tf.train.MomentumOptimizer(self.nipen_learning_rate, 0.9)
            optimizer2 = tf.train.MomentumOptimizer(self.nipen_learning_rate, 0.9)
        elif self.optimizer_method == "Adadelta":
            optimizer1 = tf.train.AdadeltaOptimizer()
            optimizer2 = tf.train.AdadeltaOptimizer()
        elif self.optimizer_method == "Adagrad":
            optimizer1 = tf.train.AdagradOptimizer(self.nipen_learning_rate)
            optimizer2 = tf.train.AdagradOptimizer(self.nipen_learning_rate)

        gvs = optimizer1.compute_gradients(self.cost, var_list=self.train_var_list1)
        capped_gvs = [(tf.clip_by_value(grad, -5., 5.), var) for grad, var in gvs]
        self.optimizer1 = optimizer1.apply_gradients(capped_gvs, global_step=self.step)
        gvs = optimizer2.compute_gradients(self.cost, var_list=self.train_var_list2)
        capped_gvs = [(tf.clip_by_value(grad, -5., 5.), var) for grad, var in gvs]
        self.optimizer2 = optimizer2.apply_gradients(capped_gvs, global_step=self.step)

    def train(self, epoch_itr):
        start_time = time.time()
        total_batch = int(self.num_doc / float(self.batch_size)) + 1
        mask_corruption_np = np.random.binomial(1, 1 - self.train_nipen_corruption_level,
                                                (self.num_doc, self.num_voca))
        random_perm_doc_idx = list(np.random.permutation(self.num_doc))
        batch_cost = 0
        batch_cost1 = 0
        batch_cost2 = 0
        batch_cost3 = 0
        batch_cost4 = 0
        batch_cost5 = 0

        for i in range(total_batch):
            if i == total_batch - 1:
                batch_set_idx = random_perm_doc_idx[i * self.batch_size:]
            elif i < total_batch - 1:
                batch_set_idx = random_perm_doc_idx[i * self.batch_size: (i + 1) * self.batch_size]

            _, Cost, Cost1, Cost2, Cost3, Cost4, Cost5 \
                = self.sess.run(
                [self.optimizer1, self.cost, self.cost1, self.cost2, self.cost3, self.cost4, self.cost5],
                feed_dict={self.model_mask_corruption: mask_corruption_np[batch_set_idx, :],
                           self.model_X: self.X_dw[batch_set_idx, :],
                           self.model_input_v_ud: self.train_v_ud[:, batch_set_idx],
                           self.model_mask_v_ud: self.mask_train_v_ud[:, batch_set_idx],
                           self.model_num_voting: self.num_train_voting,
                           self.model_keep_prob: self.train_keep_prob,
                           self.model_batch_data_idx: batch_set_idx,
                           self.num_eps_samples: 1
                           })
            _, Cost, Cost1, Cost2, Cost3, Cost4, Cost5 \
                = self.sess.run(
                [self.optimizer2, self.cost, self.cost1, self.cost2, self.cost3, self.cost4, self.cost5], \
                feed_dict={self.model_mask_corruption: mask_corruption_np[batch_set_idx, :],
                           self.model_X: self.X_dw[batch_set_idx, :],
                           self.model_input_v_ud: self.train_v_ud[:, batch_set_idx],
                           self.model_mask_v_ud: self.mask_train_v_ud[:, batch_set_idx],
                           self.model_num_voting: self.num_train_voting,
                           self.model_keep_prob: self.train_keep_prob,
                           self.model_batch_data_idx: batch_set_idx,
                           self.num_eps_samples: 1
                           })

            batch_cost = batch_cost + Cost
            batch_cost1 = batch_cost1 + Cost1
            batch_cost2 = batch_cost2 + Cost2
            batch_cost3 = batch_cost3 + Cost3
            batch_cost4 = batch_cost4 + Cost4
            batch_cost5 = batch_cost5 + Cost5

        if epoch_itr % self.display_step == 0:
            print("Training //", "Epoch %d //" % (epoch_itr), " Total cost = {:.2f}".format(batch_cost),
                  "Elapsed time : %d sec" % (time.time() - start_time))
            print("Training //", "Epoch %d //" % (epoch_itr), " Cost1 = {:.2f}".format(Cost1),
                  " Cost2 = {:.2f}".format(Cost2), " Cost3 = {:.2f}".format(Cost3), " Cost4 = {:.4f}".format(Cost1),
                  " Cost5 = {:.2f}".format(Cost5),
                  "Elapsed time : %d sec" % (time.time() - start_time))

    def test(self, itr):
        start_time = time.time()
        mask_corruption_np = np.random.binomial(1, 1 - self.train_nipen_corruption_level,
                                                (self.num_doc, self.num_voca))
        batch_set_idx = list(range(self.num_doc))
        Cost, P_V_ud \
            = self.sess.run(
            [self.cost, self.p_vud], \
            feed_dict={self.model_mask_corruption: mask_corruption_np,
                       self.model_X: self.X_dw,
                       self.model_input_v_ud: self.test_v_ud,
                       self.model_mask_v_ud: self.mask_test_v_ud,
                       self.model_num_voting: self.num_test_voting,
                       self.model_keep_prob: self.test_keep_prob,
                       self.model_batch_data_idx: batch_set_idx,
                       self.num_eps_samples: 20
                       })

        self.Estimated_R = P_V_ud.clip(min=0, max=1)
        RMSE, MAE, ACC, AVG_loglikelihood = evaluation(self.test_v_ud, self.mask_test_v_ud, self.Estimated_R,
                                                       self.num_test_voting)
        self.test_rmse_list.append(RMSE)
        self.test_mae_list.append(MAE)
        self.test_acc_list.append(ACC)
        self.test_avg_loglike_list.append(AVG_loglikelihood)

        if itr % self.display_step == 0:
            print("Testing //", "Epoch %d //" % (itr), " Total cost = {:.2f}".format(Cost),
                  "Elapsed time : %d sec" % (time.time() - start_time))
            print("RMSE = {:.4f}".format(RMSE), "MAE = {:.4f}".format(MAE), "ACC = {:.10f}".format(ACC),
                  "AVG Loglike = {:.4f}".format(AVG_loglikelihood))
            print("=" * 100)

        if RMSE <= self.min_RMSE:
            self.min_RMSE = RMSE
            self.min_epoch = itr
            self.patience = 0
        else:
            self.patience = self.patience + 1

        if (itr > 400) and (self.patience >= self.total_patience):
            self.test_rmse_list.append(self.test_rmse_list[self.min_epoch])
            self.test_mae_list.append(self.test_mae_list[self.min_epoch])
            self.test_acc_list.append(self.test_acc_list[self.min_epoch])
            self.test_avg_loglike_list.append(self.test_avg_loglike_list[self.min_epoch])
            self.earlystop_switch = True
            print("========== Early Stopping at Epoch %d" % itr)


