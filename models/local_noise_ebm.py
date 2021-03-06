import numpy as N
from theano import function, scan, shared
import theano.tensor as T
import copy
from theano.printing import Print
from theano.tensor.shared_randomstreams import RandomStreams
import theano
floatX = theano.config.floatX


class LocalNoiseEBM(object):
    def reset_rng(self):

        self.rng = N.random.RandomState([12.,9.,2.])
        self.theano_rng = RandomStreams(self.rng.randint(2**30))
        if self.initialized:
            self.redo_theano()
    #

    def __getstate__(self):
        d = copy.copy(self.__dict__)

        #remove everything set up by redo_theano

        for name in self.names_to_del:
            if name in d:
                del d[name]

        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        #self.redo_theano()      # todo: make some way of not running this, so it's possible to just open something up and look at its weights fast without recompiling it

    def weights_format(self):
        return ['v','h']

    def get_dimensionality(self):
        return 0

    def important_error(self):
        return 2

    def __init__(self, nvis, nhid,
                learning_rate, irange,
                init_bias_hid,
                init_noise_var,
                min_misclass,
                max_misclass,
                time_constant,
                noise_var_scale_up,
                noise_var_scale_down,
                max_noise_var,
                different_examples,
                energy_function,
                init_vis_prec,
                learn_vis_prec,
                vis_prec_lr_scale = 1e-2, # 0 won't make it not learn, it will just make the transfer function invalid
                init_delta = 0.0,
                clean_contrastive_coeff = 0.0,
                use_two_noise_vars = False,
                denoise = False
                ):
        self.denoise = denoise
        self.initialized = False
        self.reset_rng()
        self.nhid = nhid
        self.nvis = nvis
        self.learning_rate = learning_rate
        self.ERROR_RECORD_MODE_MONITORING = 0
        self.error_record_mode = self.ERROR_RECORD_MODE_MONITORING
        self.init_weight_mag = irange
        self.force_batch_size = 0
        self.init_bias_hid = init_bias_hid
        self.noise_var = shared(N.cast[floatX] (init_noise_var))
        self.min_misclass = min_misclass
        self.max_misclass = max_misclass
        self.time_constant = time_constant
        self.noise_var_scale_up = noise_var_scale_up
        self.noise_var_scale_down = noise_var_scale_down
        self.max_noise_var = max_noise_var
        self.misclass = -1
        self.different_examples = different_examples
        self.init_vis_prec = init_vis_prec
        self.learn_vis_prec = learn_vis_prec
        self.vis_prec_lr_scale = vis_prec_lr_scale
        self.energy_function = energy_function
        self.init_delta = init_delta
        self.use_two_noise_vars = use_two_noise_vars
        self.clean_contrastive_coeff = clean_contrastive_coeff

        self.names_to_del = []

        self.redo_everything()

    def set_error_record_mode(self, mode):
        self.error_record_mode = mode

    def set_size_from_dataset(self, dataset):
        self.nvis = dataset.get_output_dim()
        self.redo_everything()
        self.vis_mean.set_value( dataset.get_marginals(), borrow=False)
    #

    def get_input_dim(self):
        return self.nvis

    def get_output_dim(self):
        return self.nhid

    def redo_everything(self):
        self.initialized = True

        self.error_record = []
        self.examples_seen = 0
        self.batches_seen = 0

        self.W = shared( N.cast[floatX](self.rng.uniform(-self.init_weight_mag, self.init_weight_mag, (self.nvis, self.nhid ) ) ))
        self.W.name = 'W'

        self.b = shared( N.cast[floatX](N.zeros(self.nhid) + self.init_bias_hid) )
        self.b.name = 'b'

        self.c = shared( N.cast[floatX](N.zeros(self.nvis)))
        self.c.name = 'c'

        self.params = [ self.W, self.c, self.b ]


        self.vis_prec_driver = shared(N.zeros(self.nvis) + N.log(N.exp(self.init_vis_prec) - 1.) / self.vis_prec_lr_scale)
        self.vis_prec_driver.name = 'vis_prec_driver'

        assert not N.any(N.isnan( self.vis_prec_driver.get_value() ))
        assert not N.any(N.isinf( self.vis_prec_driver.get_value() ))


        if self.learn_vis_prec:
            self.params.append(self.vis_prec_driver)
        #

        if self.energy_function == 'mse autoencoder':
            self.delta = shared(self.init_delta + N.zeros(self.nhid))
            self.delta.name = 'delta'
            self.s = shared(N.ones(self.nhid))
            self.s.name = 's'
            self.params.append(self.s)
            if not self.denoise:
                self.params.append(self.delta)
        #


        self.redo_theano()
    #


    def batch_energy(self, V, H):

        if self.energy_function != 'gaussian-binary rbm':
            assert False

        output_scan, updates = scan(
                 lambda v, h, beta: 0.5 * T.dot(v,beta*v) - T.dot(self.b,h) - T.dot(self.c,v) -T.dot(v,T.dot(self.W,h)),
                 sequences  = (V,H), non_sequences = self.vis_prec)


        return output_scan

    def p_h_given_v(self, V):
        if self.energy_function != 'gaussian-binary rbm':
            assert False

        return T.nnet.sigmoid(self.b + T.dot(V,self.W))

    def free_energy(self, V):
        return self.batch_free_energy(V)

    def batch_free_energy(self, V):

        if self.energy_function == 'gaussian-binary rbm':
            output_scan, updates = scan(
                lambda v, beta: 0.5 * T.dot(v,beta * v) - T.dot(self.c,v) - T.sum(T.nnet.softplus( T.dot(v,self.W)+self.b)),
                 sequences  = V,  non_sequences = self.vis_prec
                 )
        elif self.energy_function == 'mse autoencoder':


            def fn(v, beta, w):
                h = T.nnet.sigmoid((self.s/w) * T.dot(v,self.W)-self.s+self.b)
                h.name = 'h'
                r = T.dot(self.W,h)+self.c
                r.name = 'r'

                assert len(h.type().broadcastable ) == 1
                assert len(self.delta.type().broadcastable ) == 1

                penalty =  - T.dot(self.delta , h)

                d = v -r

                scaled_mse = T.dot(d,beta * d)

                rval =  scaled_mse + penalty

                assert len(rval.type().broadcastable ) == 0

                return rval

            output_scan, updates = scan(
                    fn,
                    sequences = V, non_sequences = [self.vis_prec, self.wnorms])

        assert len(output_scan.type().broadcastable ) == 1

        return output_scan

    def redo_theano(self):

        if 'denoise' not in dir(self):
            self.denoise = False

        if 'energy_function' not in dir(self):
            self.energy_function = 'gaussian-binary rbm'

        if 'noise_var' not in dir(self):
            self.noise_var = self.beta
            del self.beta

        if 'different_examples' not in dir(self):
            self.different_examples = False

        if 'vis_prec_driver' not in dir(self):
            self.vis_prec_lr_scale = 1.
            self.vis_prec_driver = shared(N.zeros(self.nvis) + N.log(N.exp(1.0) - 1.) / self.vis_prec_lr_scale)


        pre_existing_names = dir(self)

        self.wnorms = T.sum(T.sqr(self.W),axis=0)

        self.vis_prec = T.nnet.softplus(self.vis_prec_driver *  self.vis_prec_lr_scale)


        self.vis_prec.name = 'vis_prec'

        self.W_T = self.W.T
        self.W_T.name = 'W.T'

        alpha = T.scalar()

        X = T.matrix()
        X.name = 'X'


        if self.use_two_noise_vars:
            switch = self.theano_rng.normal(size=[1,], avg = 0, std = 1, dtype='float32') > 0.0
        else:
            switch = 1.0

        final_noise_var = switch * self.noise_var + (1.0 - switch)* 2.0

        corrupted = self.theano_rng.normal(size = X.shape, avg = X,
                                    std = T.sqrt(final_noise_var), dtype = X.dtype)


        corrupted.name = 'prenorm_corrupted'

        old_norm = T.sqr(X).sum(axis=1)
        old_norm.name = 'old_norm'


        new_norm = T.sqr(corrupted).sum(axis=1)
        new_norm.name = 'new_norm'


        norm_ratio = old_norm / (1e-8 + new_norm)
        norm_ratio.name = 'norm_ratio'

        norm_ratio_shuffled = norm_ratio.dimshuffle(0,'x')
        norm_ratio_shuffled.name = 'norm_ratio_shuffled'


        #corrupted = corrupted * norm_ratio_shuffled
        #corrupted.name = 'postnorm_corrupted'

        print "NOT USING NORM RESCALING"

        self.corruption_func = function([X],corrupted)



        E_c = self.batch_free_energy(corrupted)

        E_c.name = 'E_c'

        if self.different_examples:
            X2 = T.matrix()
            inputs = [ X, X2]
        else:
            X2 = X
            inputs = [ X ]
        #

        E_d = self.batch_free_energy(X2)
        assert len(E_d.type().broadcastable) == 1

        E_d.name = 'E_d'


        noise_contrastive = T.mean(
                -T.log(
                    T.nnet.sigmoid(
                        E_c - E_d)   ) )


        if self.denoise:
            H = h = T.nnet.sigmoid((self.s/self.wnorms) * T.dot(corrupted,self.W)-self.s+self.b)
            H.name = 'H'
            R = (T.dot(H,self.W.T)+self.c)/self.vis_prec

            recons_diff = R - X

            #obj = T.mean(T.sqr(recons_diff))



            model_score_diffs = corrupted - R
            noise_dir = corrupted - X

            model_score = self.vis_prec * model_score_diffs
            model_score.name = 'model_score'

            data_score = noise_dir / self.noise_var

            score_diffs = data_score  - model_score


            obj = T.mean(T.sqr(score_diffs ))



            HX = T.nnet.sigmoid((self.s/self.wnorms) * T.dot(X,self.W)-self.s+self.b)
            RX = T.dot(HX,self.W.T)+self.c

            recons_diff_X = RX - X

            recons_norms = T.sum(T.sqr(recons_diff_X),axis=1)

            recons_dir = recons_diff_X / (1e-14+T.sqrt(recons_norms.dimshuffle((0,'x'))))

            self.recons_dir_func = function( [X], recons_dir)

        elif self.clean_contrastive_coeff > 0:
            assert not self.different_examples

            E_d_0 = self.batch_free_energy(X)

            clean_contrastive = T.mean(
                -T.log(T.nnet.sigmoid( E_d - E_d_0)))

            obj =  noise_contrastive + self.clean_contrastive_coeff * clean_contrastive
        else:
            obj = noise_contrastive



        self.error_func = function(inputs,obj )

        misclass_batch = (E_c < E_d)
        misclass_batch.name = 'misclass_batch'

        misclass = misclass_batch.mean()
        misclass.name = 'misclass'

        #print 'maker'
        #print theano.printing.debugprint(self.error_func.maker.env.outputs[0])
        #print 'obj'
        #print theano.printing.debugprint(obj)

        self.E_d_func = function(inputs, E_d.mean())
        self.E_d_batch_func = function(inputs, E_d)
        self.E_X_batch_func =  function([X2], E_d)
        self.E_c_func = function(inputs, E_c.mean())
        self.sqnorm_grad_E_c_func = function(inputs, T.sum(T.sqr(T.grad(T.mean(E_c),corrupted))))
        self.sqnorm_grad_E_d_func = function(inputs, T.sum(T.sqr(T.grad(T.mean(E_d),X2))))

        self.misclass_func = function(inputs, misclass)




        #self.norm_misclass_func = function([X], ( T.sum(T.sqr(corrupted),axis=1) < T.sum(T.sqr(X),axis=1) ).mean())
        #self.norm_c_func = function([X], T.sum(T.sqr(corrupted),axis=1).mean())
        #self.norm_d_func = function([X], T.sum(T.sqr(X),axis=1).mean())

        grads = [ T.grad(obj,param) for param in self.params ]

        learn_inputs = [ ipt for ipt in inputs ]
        learn_inputs.append(alpha)

        self.learn_func = function(learn_inputs, updates =
                [ (param, param - alpha * grad) for (param,grad)
                    in zip(self.params, grads) ] , name='learn_func')

        if self.energy_function != 'mse autoencoder':
            self.recons_func = function([X], self.gibbs_step_exp(X) , name = 'recons_func')
        #

        post_existing_names = dir(self)

        self.names_to_del = [ name for name in post_existing_names if name not in pre_existing_names]

    def learn(self, dataset, batch_size):
        self.learn_mini_batch([dataset.get_batch_design(batch_size) for x in xrange(1+self.different_examples)])


    def recons_func(self, x):
        rval = N.zeros(x.shape)
        for i in xrange(x.shape[0]):
            rval[i,:] = self.gibbs_step_exp(x[i,:])

        return rval


    def print_suite(self, dataset, batch_size, batches,  things_to_print):
        self.theano_rng.seed(5)

        tracker =  {}

        for thing in things_to_print:
            tracker[thing[0]] = []

        for i in xrange(batches):
            x = dataset.get_batch_design(batch_size)
            assert x.shape == (batch_size, self.nvis)

            if self.different_examples:
                inputs = [ x , dataset.get_batch_design(batch_size) ]
            else:
                inputs = [ x ]

            for thing in things_to_print:
                tracker[thing[0]].append(thing[1](*inputs))

        for thing in things_to_print:
            print thing[0] + ': '+str(N.asarray(tracker[thing[0]]).mean())
        #
    #

    def record_monitoring_error(self, dataset, batch_size, batches):
        assert self.error_record_mode == self.ERROR_RECORD_MODE_MONITORING

        print 'noise variance (before norm rescaling): '+str(self.noise_var.get_value())

        #always use the same seed for monitoring error
        self.theano_rng.seed(5)

        errors = []

        misclasses = []

        for i in xrange(batches):
            x = dataset.get_batch_design(batch_size)
            assert x.shape == (batch_size, self.nvis)

            if self.different_examples:
                inputs = [ x, dataset.get_batch_design(batch_size) ]
            else:
                inputs = [ x ]

            error = self.error_func(*inputs)
            errors.append( error )
            misclass = self.misclass_func(*inputs)
            misclasses.append(misclass)
        #

        misclass = N.asarray(misclasses).mean()

        print 'misclassification rate: '+str(misclass)

        error = N.asarray(errors).mean()

        assert not N.isnan(misclass)
        assert not N.isnan(error)

        self.error_record.append( (self.examples_seen, self.batches_seen, error, self.noise_var.get_value(), misclass ) )

        print "TODO: restore old theano_rng state instead of jumping to new one"
        self.theano_rng.seed(self.rng.randint(2**30))
    #

    def reconstruct(self, x, use_noise):
        assert x.shape[0] == 1

        print 'x summary: '+str((x.min(),x.mean(),x.max()))

        #this method is mostly a hack to make the formatting work the same as denoising autoencoder
        self.truth_shared = shared(x.copy())

        if use_noise:
            self.vis_shared = shared(self.corruption_func(x))
        else:
            self.vis_shared = shared(x.copy())

        self.reconstruction = self.recons_func(self.vis_shared.get_value())

        print 'recons summary: '+str((self.reconstruction.min(),self.reconstruction.mean(),self.reconstruction.max()))


    def gibbs_step_exp(self, V):
        base_name = V.name

        if base_name is None:
            base_name = 'anon'

        Q = self.p_h_given_v(V)
        H = self.sample_hid(Q)

        H.name =  base_name + '->hid_sample'

        sample =  self.c + T.dot(H,self.W_T)

        sample.name = base_name + '->sample_expectation'

        return sample


    def sample_hid(self, Q):
        return self.theano_rng.binomial(size = Q.shape, n = 1, p = Q,
                                dtype = Q.dtype)


    def learn_mini_batch(self, inputs):

        for x in inputs:
            assert x.shape[1] == self.nvis

        cur_misclass = self.misclass_func(*inputs)

        if self.misclass == -1:
            self.misclass = cur_misclass
        else:
            self.misclass = self.time_constant * cur_misclass + (1.-self.time_constant) * self.misclass

        #print 'current misclassification rate: '+str(self.misclass)

        if self.misclass > self.max_misclass:
            self.noise_var.set_value(min(self.max_noise_var,self.noise_var.get_value() * self.noise_var_scale_up) )
        elif self.misclass < self.min_misclass:
            self.noise_var.set_value(max(1e-8,self.noise_var.get_value() * self.noise_var_scale_down ))
        #

        learn_inputs = [ ipt for ipt in inputs ]
        learn_inputs.append(self.learning_rate)
        self.learn_func( * learn_inputs)



        self.examples_seen += x.shape[0]
        self.batches_seen += 1
    #
#

