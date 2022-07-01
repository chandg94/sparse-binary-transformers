import torch
import torch.autograd as autograd
import torch.nn as nn
import torch.nn.functional as F
#from torch.nn.utils import weight_norm as wn
import math


from utils.initializations import _init_weight,_init_score
import numpy as np

class GetQuantnet_binary(autograd.Function):
    @staticmethod
    def forward(ctx, scores, weights, k):
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
        q_weight = abs_wgt * out # Remove pruned weights
        num_unpruned = int(k * scores.numel()) # Number of unpruned weights
        alpha = torch.sum(q_weight) / num_unpruned # Compute alpha = || q_weight ||_1 / (number of unpruned weights)

        # Save absolute value of weights for backward
        ctx.save_for_backward(abs_wgt)

        # Return pruning mask with gain term alpha for binary weights
        return alpha * out

    @staticmethod
    def backward(ctx, g):
        # Get absolute value of weights from saved ctx
        abs_wgt, = ctx.saved_tensors
        # send the gradient g times abs_wgt on the backward pass
        return g * abs_wgt, None, None

class SubnetConvBiprop(nn.Linear):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.scores = nn.Parameter(torch.Tensor(self.weight.size()))
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

    def rerandomize(self):
        with torch.no_grad():
            if self.args.rerand_type == 'recycle':
                sorted, indices = torch.sort(self.scores.abs().flatten())
                k = int((self.args.rerand_rate) * self.scores.numel())
                low_scores=indices[:k]
                if self.args.ablation:
                    print("Ablation recycling")
                    high_scores=indices[k:2*k]
                else:
                    high_scores=indices[-k:]
                self.weight.flatten()[low_scores]=self.weight.flatten()[high_scores]
                print('recycling {} out of {} weights'.format(k,self.weight.numel()))

            elif self.args.rerand_type == 'iterand':
                self.args.weight_seed += 1
                weight_twin = torch.zeros_like(self.weight)
                weight_twin = _init_weight(self.args, weight_twin)

                ones = torch.ones(self.weight.size()).to(self.weight.device)
                b = torch.bernoulli(ones * self.args.rerand_rate)
                mask=GetQuantnet_binary.apply(self.clamped_scores, self.weight, self.prune_rate)
                t1 = self.weight.data * mask
                t2 = self.weight.data * (1 - mask) * (1 - b)
                t3 = weight_twin.data * (1 - mask) * b
                self.weight.data = t1 + t2 +t3


                print('rerandomizing {} out of {} weights'.format(torch.sum(b), self.weight.numel()))

    def forward(self, x):

        # Get binary mask and gain term for subnetwork
        quantnet = GetQuantnet_binary.apply(self.clamped_scores, self.weight, self.prune_rate)
        # Binarize weights by taking sign, multiply by pruning mask and gain term (alpha)
        w = torch.sign(self.weight) * quantnet
        # Pass binary subnetwork weights to convolution layer
        x= F.linear(
            x, w, self.bias
        )
        # Return output from linear layer
        return x
