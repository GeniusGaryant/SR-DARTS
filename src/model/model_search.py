import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from model.operations import *
from model.genotypes import PRIMITIVES
from model.genotypes import Genotype


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

    def __init__(self, steps, multiplier, C_prev_prev, C_prev, C, reduction, reduction_prev):
        super(Cell, self).__init__()
        self.reduction = reduction

        self.preprocess0 = ReLUConv(C_prev_prev, C, 3, 1, 1, affine=False)
        self.preprocess1 = ReLUConv(C_prev, C, 3, 1, 1, affine=False)
        self._steps = steps
        self._multiplier = multiplier

        self._ops = nn.ModuleList()
        # self._bns = nn.ModuleList()
        for i in range(self._steps):
            for j in range(2+i):
                # cancel the reduction cell
                # stride = 1 if reduction and j < 2 else 1
                stride = 1
                op = MixedOp(C, stride)
                self._ops.append(op)

    def forward(self, s0, s1, weights):
        s0 = self.preprocess0(s0)
        s1 = self.preprocess1(s1)

        states = [s0, s1]
        offset = 0
        for i in range(self._steps):
            s = sum(self._ops[offset+j](h, weights[offset+j])
                    for j, h in enumerate(states))
            offset += len(states)
            states.append(s)

        return torch.cat(states[-self._multiplier:], dim=1)


class Network(nn.Module):
    """
    Network
    """
    def __init__(self, args, loss):
        super(Network, self).__init__()
        self.args = args
        self._C = args.init_channels
        self._layers = args.layers
        self._scale = args.scale[0]
        self._criterion = loss
        self._steps = 4
        self._multiplier = 4
        self.stem_multiplier = 3

        C_curr = self.stem_multiplier * self._C
        self.stem = nn.Sequential(
            nn.Conv2d(3, C_curr, 3, padding=1, bias=False),
        )

        C_prev_prev, C_prev, C_curr = C_curr, C_curr, self._C
        self.cells = nn.ModuleList()
        reduction_prev = False
        for i in range(self._layers):
            # if i in [self._layers // 3, 2 * self._layers//3]:
            #     # C_curr *= 2
            #     reduction = True
            # else:
            #     reduction = False
            reduction = False
            cell = Cell(self._steps, self._multiplier, C_prev_prev, C_prev,
                        C_curr, reduction, reduction_prev)
            reduction_prev = reduction
            self.cells += [cell]
            C_prev_prev, C_prev = C_prev, self._multiplier * C_curr

        self.upsampler = Upsampler(C_prev, C_prev, 3,
                                   stride=1, padding=1, scale=self._scale)
        self.channel_reducer = nn.Sequential(
            nn.Conv2d(C_prev, args.n_colors, 3, padding=1, bias=False)
        )

        self._initialize_alphas()

    def new(self):
        model_new = Network(self.args, self._criterion).cuda()
        for x, y in zip(model_new.arch_parameters(), self.arch_parameters()):
            x.data.copy_(y.data)
        return model_new

    def forward(self, input, temperature):
        s0 = s1 = self.stem(input)
        for i, cell in enumerate(self.cells):
            # if cell.reduction:
            #     weights = F.softmax(self.alphas_reduce, dim=-1)
            # else:
            #     weights = F.softmax(self.alphas_normal, dim=-1)
            # weights = F.softmax(self.alphas_normal, dim=-1)
            weights = F.softmax(self.alphas_normal / temperature, dim=-1)
            s0, s1 = s1, cell(s0, s1, weights)
        out = self.upsampler(s1)
        logits = self.channel_reducer(out)
        return logits

    def _loss(self, input, target, temperature):
        logits = self(input, temperature)
        return self._criterion(logits, target)

    def _initialize_alphas(self):
        k = sum(1 for i in range(self._steps) for n in range(2+i))
        num_ops = len(PRIMITIVES)

        self.alphas_normal = Variable(
            1e-3 * torch.randn(k, num_ops).cuda(), requires_grad=True)
        # self.alphas_reduce = Variable(
        #     1e-3 * torch.randn(k, num_ops).cuda(), requires_grad=True)
        self._arch_parameters = [
            self.alphas_normal,
            # self.alphas_reduce,
        ]

    def arch_parameters(self):
        return self._arch_parameters

    def genotype(self):

        def _parse(weights):
            gene = []
            n = 2
            start = 0
            for i in range(self._steps):
                end = start + n
                W = weights[start:end].copy()
                edges = sorted(range(i + 2), key=lambda x: -max(
                    W[x][k] for k in range(len(W[x])) if k != PRIMITIVES.index('none')))[:2]
                for j in edges:
                    k_best = None
                    for k in range(len(W[j])):
                        if k != PRIMITIVES.index('none'):
                            if k_best is None or W[j][k] > W[j][k_best]:
                                k_best = k
                    gene.append((PRIMITIVES[k_best], j))
                start = end
                n += 1
            return gene

        gene_normal = _parse(
            F.softmax(self.alphas_normal, dim=-1).data.cpu().numpy())
        # gene_reduce = _parse(
        #     F.softmax(self.alphas_reduce, dim=-1).data.cpu().numpy())

        concat = range(2+self._steps-self._multiplier, self._steps+2)
        genotype = Genotype(
            normal=gene_normal, normal_concat=concat,
            # reduce=gene_reduce, reduce_concat=concat
        )
        return genotype
