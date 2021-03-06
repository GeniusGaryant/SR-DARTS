import torch
import torch.nn as nn
from model.operations import *
from model.genotypes import PRIMITIVES


class MixedOp(nn.Module):

    def __init__(self, C, stride):
        super(MixedOp, self).__init__()
        self._ops = nn.ModuleList()
        for primitive in PRIMITIVES:
            op = OPS[primitive](C, stride, False)
            if 'pool' in primitive:
                op = nn.Sequential(op, nn.BatchNorm2d(C, affine=False))
            self._ops.append(op)

    def forward(self, x, weights):
        return sum(w * op(x) for w, op in zip(weights, self._ops))


class Cell(nn.Module):

    def __init__(self, n_nodes, multiplier, C_prev_prev, C_prev, C_curr):
        super(Cell, self).__init__()

        self.preprocess0 = ReLUConv(C_prev_prev, C_curr, 3, 1, 1, affine=False)
        self.preprocess1 = ReLUConv(C_prev, C_curr, 3, 1, 1, affine=False)
        self.n_nodes = n_nodes
        self.multiplier = multiplier

        self._ops = nn.ModuleList()
        # self._bns = nn.ModuleList()
        for i in range(self.n_nodes):
            for j in range(2+i):
                # reduce feature map in reduce cell
                # stride = 1 if reduction and j < 2 else 1
                stride = 1
                op = MixedOp(C_curr, stride)
                self._ops.append(op)

    def forward(self, s0, s1, weights):
        s0 = self.preprocess0(s0)
        s1 = self.preprocess1(s1)

        states = [s0, s1]
        offset = 0
        for i in range(self.n_nodes):
            s = sum(self._ops[offset+j](h, weights[offset+j])
                    for j, h in enumerate(states))
            offset += len(states)
            states.append(s)

        return torch.cat(states[-self.multiplier:], dim=1)
