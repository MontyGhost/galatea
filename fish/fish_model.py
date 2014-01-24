#! /usr/bin/env python

# Standard library imports
#import functools
#from itertools import izip
#from collections import OrderedDict
#import operator
import ipdb as pdb

# Third-party imports
import numpy
import theano
from theano import tensor
#from theano.tensor.nnet.conv import conv2d
#from theano.tensor.nnet.conv3d2d import conv3d

# Local imports
#from pylearn2.models import Model
from pylearn2.models.mlp import MLP
#from pylearn2.models.mlp import MLP, SpaceConverter
#from pylearn2.models.autoencoder import Autoencoder, SpaceAwareAutoencoderWrapper
#from pylearn2.utils import sharedX
#from pylearn2.utils.theano_graph import is_pure_elemwise
#from pylearn2.utils.misc import Container
#from pylearn2.space import Space, VectorSpace, Conv2DSpace

#from pylearn2.costs.prednet import PredNetCost

#theano.config.warn.sum_div_dimshuffle_bug = False
#theano.config.compute_test_value = 'raise'
#theano.config.exception_verbosity = 'high'


class Container(object):
    pass



class FishMLP(MLP):
    '''
    A slight extension of MLP.
    '''

    def __init__(self, layers, num_frames, batch_size=None,
                 input_space=None, nvis=None, seed=None):
        '''Takes the same arguments as MLP, except also requires
        num_frames: the number of consecutive frames to expect in each
        batch.
        '''

        super(FishMLP, self).__init__(layers,
                                      batch_size = batch_size,
                                      input_space = input_space,
                                      nvis = nvis,
                                      seed = seed)

        self.num_frames = num_frames
        assert num_frames > 0, 'num_frames must be positive'

        ####### HACK because MLP doesn't call super class init
        self.names_to_del = getattr(self, 'names_to_del', set())
        self._test_batch_size = getattr(self, '_test_batch_size', 2)
        ####### HACK because MLP doesn't call super class init

        self.redo_theano()

    def get_target_source(self):
        '''Override default Model.get_target_source'''
        return ('targets1', 'targets2')
    
    def redo_theano(self):
        '''Create compiled versions of all relevant functions'''

        # Call base version, in case it's not empty
        super(FishMLP, self).redo_theano()
        
        self.register_names_to_del('fns')

        self.fns = Container()

        # 0. Create example inputs/outputs
        # Size: ('c', 0, 1, 'b')
        example_batch_size = self.get_test_batch_size()  # e.g. 2
        id_space, xy_space = self.get_output_space().components      # Hard coded order here!!
        assert id_space.dim in (10,25), 'maybe wrong order of spaces?'
        assert xy_space.dim in (4,6),   'maybe wrong order of spaces?'

        # 0.1 Shapes
        example_feat_shape = ((self.input_space.num_channels,) +  # e.g. 1
                              self.input_space.shape +            # e.g. (156,156)
                              (example_batch_size,))              # e.g. 2
        example_id1H_shape = (example_batch_size, id_space.dim)   # e.g. (2,25)
        example_idN_shape = (example_batch_size,)                 # e.g. (2,)
        example_xy_shape = (example_batch_size, xy_space.dim)     # e.g. (2,4)
        # 0.2 Make zero matrices
        example_feat = numpy.zeros(example_feat_shape,
                                   dtype=theano.config.floatX)
        example_id1H = numpy.zeros(example_id1H_shape,
                                   dtype=theano.config.floatX)
        example_idN = numpy.zeros(example_idN_shape,
                                   dtype=theano.config.floatX)
        example_xy = numpy.zeros(example_xy_shape,
                                 dtype=theano.config.floatX)

        # 1. Construct computation graph (internal to this instance)
        # Note: this part is messy. We should eventually encapsulate
        # scope not via ugly unique names, but by defining within a
        # function
        _feat0 = theano.tensor.tensor4('_feat0')
        _feat0.tag.test_value = example_feat
        _id1H0, _xy0 = self.feat_to_compout(_feat0)
        _id1H0.name = '_id1H0'   # rename
        _xy0.name = '_xy0'       # rename
        self.fns.feat_to_compout = theano.function(
            [_feat0], [_id1H0, _xy0],
            name = 'fns.feat_to_compout')

        _feat1 = theano.tensor.tensor4('_feat1')
        _feat1.tag.test_value = example_feat
        _ids1 = self.feat_to_idsN(_feat1)
        _ids1.name = '_ids1'
        self.fns.feat_to_idsN = theano.function(
            [_feat1], _ids1,
            name = 'fns.feat_to_idsN')

        _feat2 = theano.tensor.tensor4('_feat2')
        _feat2.tag.test_value = example_feat
        _xy2 = self.feat_to_xy(_feat2)
        _xy2.name = '_xy2'
        self.fns.feat_to_xy = theano.function(
            [_feat2], _xy2,
            name = 'fns.feat_to_xy')

        _feat3 = theano.tensor.tensor4('_feat3')
        _feat3.tag.test_value = example_feat
        _ids_true3 = theano.tensor.vector('_ids_true3')
        _ids_true3.tag.test_value = example_idN
        _acc3 = self.wiskott_id_accuracy(_feat3, _ids_true3)
        _acc3.name = '_acc3'
        self.fns.wiskott_id_accuracy= theano.function(
            [_feat3, _ids_true3], _acc3,
            name = 'fns.wiskott_id_accuracy')

        _feat4 = theano.tensor.tensor4('_feat4')
        _feat4.tag.test_value = example_feat
        _xy_true4 = theano.tensor.matrix('_xy_true4')
        _xy_true4.tag.test_value = example_xy[:,0:2]      # xy is first two columns
        _xy_errs4 = self.wiskott_xy_errors(_feat4, _xy_true4)
        _xy_errs4.name = '_xy_errs4'
        self.fns.wiskott_xy_errors= theano.function(
            [_feat4, _xy_true4], _xy_errs4,
            name = 'fns.wiskott_xy_errors')

        # 2. At this point, if compute_test_value mode is on, all
        # functions should have already been tested (when they were
        # defined). However, we can also test them ourselves in the
        # absence of compute_test_value mode (because this mode seems
        # to fail for other reasons).
        run_extra_tests = True
        if run_extra_tests:
            junk = self.fns.feat_to_compout(example_feat)
            junk = self.fns.feat_to_idsN(example_feat)
            junk = self.fns.wiskott_id_accuracy(example_feat, example_idN)

    def feat_to_compout(self, feat):
        '''
        Compute composite layer output (one hot id, xysincos) given input features.

        Parameters
        ----------
        feat : tensor_like
            Theano symbolic representing the input
            minibatch(es) to be encoded and reconstructed. Assumed to be
            a 4-tensor in the space self.input_space

        Returns
        -------
        compout_tuple : 2-tuple
            Tuple of two Theano symbolics representing the
            corresponding (one hot id,xysincos) given the input features.
        '''

        compout_tuple = self.fprop(feat)
        for ii,component in enumerate(compout_tuple):
            component.name = 'compout_tuple__component_%d' % ii
        
        return compout_tuple

    def feat_to_idsN(self, feat):
        '''
        Compute numerical (not 1-hot) IDs given input features.

        Parameters
        ----------
        feat : tensor_like
            Theano symbolic representing the input
            minibatch(es) to be encoded and reconstructed. Assumed to be
            a 4-tensor in the space self.input_space

        Returns
        -------
        ids : tensor
            Theano symbolic of IDs.
        '''

        compout_tuple = self.feat_to_compout(feat)

        softmax_ids1H = compout_tuple[0]
        softmax_ids1H.name = 'softmax_ids1H'
        
        idsN = tensor.argmax(softmax_ids1H, axis=1)
        idsN.name = 'idsN'

        return idsN

    def feat_to_xy(self, feat):
        '''
        Compute predicted xy position given input features.

        Parameters
        ----------
        feat : tensor_like
            Theano symbolic representing the input
            minibatch(es) to be encoded and reconstructed. Assumed to be
            a 4-tensor in the space self.input_space

        Returns
        -------
        xy_hat : tensor
            Theano symbolic of predicted xy location (2-tensor of shape (batch_size, 2))
        '''

        compout_tuple = self.feat_to_compout(feat)

        xysincos = compout_tuple[1]
        xysincos.name = 'xysincos'

        xy_hat = xysincos[:,0:2]      # xy is always first two columns for fish or spheres
        xy_hat.name = 'xy_hat'

        return xy_hat

    def wiskott_id_accuracy(self, feat, ids_true):
        '''
        Compute the accuracy of the IDs, as in franzius2008invariant-object-recognition.

        Parameters
        ----------
        feat : tensor_like
            Theano symbolic representing the input
            minibatch(es) to be encoded and reconstructed. Assumed to be
            a 4-tensor in the space self.input_space
        ids_true : tensor_like
            Theano symbolic representing the true numerical (not 1-hot)
            IDs. Assumed to be a 1-tensor. NOTE: this should be passed in with type floatX!
            Perhaps via:
                idsN_floatX = array(idsN_int, dtype=theano.config.floatX)
        
        Returns
        -------
        accuracy : scalar floatX
            Theano scalar accuracy, e.g. 1 is 100%, 0 is 0% accuracy.
        '''

        ids_hat = self.feat_to_idsN(feat)

        accuracy_tmp = tensor.eq(ids_hat, ids_true).mean()
        accuracy_tmp.name = 'accuracy_tmp'
        
        accuracy = tensor.cast(accuracy_tmp, theano.config.floatX)
        accuracy.name = 'accuracy'
        
        return accuracy

    def wiskott_xy_errors(self, feat, xy_true):
        '''
        Compute the accuracy of x and y, as in franzius2008invariant-object-recognition.

        Parameters
        ----------
        feat : tensor_like
            Theano symbolic representing the input
            minibatch(es) to be encoded and reconstructed. Assumed to be
            a 4-tensor in the space self.input_space
        xy_true : tensor_like
            Theano symbolic representing the true xy position.
            Assumed to be a 2-tensor with shape (batch_size,2)
        
        Returns
        -------
        errors : scalararray         floatX
            ========== Theano scalar accuracy, e.g. 1 is 100%, 0 is 0% accuracy.
        '''

        xy_hat = self.feat_to_xy(feat)

        xy_errors = tensor.sqrt(tensor.sqr(xy_hat-xy_true).mean(0)) / 2.0     # The range of values is 2.0, from -1 to 1
        xy_errors.name = 'xy_errors'
        
        return xy_errors
