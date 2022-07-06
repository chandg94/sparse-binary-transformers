import torch
import torch.autograd as autograd
import torch.nn as nn
import torch.nn.functional as F
#from torch.nn.utils import weight_norm as wn
import math


from utils.model_utils import _init_weight,_init_score
import numpy as np


def linear_init(in_dim, out_dim, bias=None, args=None, **factory_kwargs):
    layer=SubnetConvBiprop(in_dim,out_dim, bias,**factory_kwargs)
    layer.init(args)
    return layer

class GetQuantnet_binary(autograd.Function):
    @staticmethod
    def forward(ctx, scores, weights, k, alpha):
        # Get the subnetwork by sorting the scores and using the top k%
        out = scores.clone()
        _, idx = scores.flatten().sort()
        j = int((1 - k) * scores.numel())
        # flat_out and out access the same memory. switched 0 and 1
        flat_out = out.flatten()
        flat_out[idx[:j]] = 0
        flat_out[idx[j:]] = 1

        # Perform binary quantization of weights
        abs_wgt = torch.abs(weights.clone()) # Absolute value of original weights
        #q_weight = abs_wgt * out # Remove pruned weights
        #num_unpruned = int(k * scores.numel()) # Number of unpruned weights
        #alpha = torch.sum(q_weight) / num_unpruned # Compute alpha = || q_weight ||_1 / (number of unpruned weights)

        # Save absolute value of weights for backward
        ctx.save_for_backward(abs_wgt)

        # Return pruning mask with gain term alpha for binary weights
        return alpha * out

    @staticmethod
    def backward(ctx, g):
        # Get absolute value of weights from saved ctx
        abs_wgt, = ctx.saved_tensors
        # send the gradient g times abs_wgt on the backward pass
        return g * abs_wgt, None, None, None

class SubnetConvBiprop(nn.Linear):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.scores = nn.Parameter(torch.Tensor(self.weight.size()))
        self.register_buffer('alpha' , torch.tensor(1, requires_grad=False))
        nn.init.kaiming_uniform_(self.scores, a=math.sqrt(5))

    @property
    def clamped_scores(self):
        # For unquantized activations
        return self.scores.abs()

    def init(self,args):
        self.args=args
        self.weight=_init_weight(self.args, self.weight)
        self.scores=_init_score(self.args, self.scores)
        self.prune_rate=args.prune_rate

    #@property
    def calc_alpha(self):
        abs_wgt = torch.abs(self.weight.clone()) # Absolute value of original weights
        q_weight = abs_wgt * self.scores.abs() # Remove pruned weights
        num_unpruned = int(self.prune_rate * self.scores.numel()) # Number of unpruned weights
        self.alpha = torch.sum(q_weight) / num_unpruned # Compute alpha = || q_weight ||_1 / (number of unpruned weights)
        return self.alpha

    def forward(self, x):

        # Get binary mask and gain term for subnetwork
        quantnet = GetQuantnet_binary.apply(self.clamped_scores, self.weight, self.prune_rate, self.calc_alpha())
        # Binarize weights by taking sign, multiply by pruning mask and gain term (alpha)
        w = torch.sign(self.weight) * quantnet
        # Pass binary subnetwork weights to convolution layer
        x= F.linear(
            x, w, self.bias
        )
        # Return output from linear layer
        return x
