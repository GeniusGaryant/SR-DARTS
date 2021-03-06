import math
import torch
import torch.nn as nn
import torch.nn.functional as F

OPS = {
    'none': lambda C, stride, affine: Zero(stride),
    'avg_pool_3x3': lambda C, stride, affine: nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False),
    'max_pool_3x3': lambda C, stride, affine: nn.MaxPool2d(3, stride=stride, padding=1),
    'skip_connect': lambda C, stride, affine: Identity() if stride == 1 else FactorizedReduce(C, C, affine=affine),
    'conv_1x1': lambda C, stride, affine: ReLUConv(C, C, 1, stride, 0, affine=affine),
    'conv_3x3': lambda C, stride, affine: ReLUConv(C, C, 3, stride, 1, affine=affine),
    'sep_conv_3x3': lambda C, stride, affine: SepConv(C, C, 3, stride, 1, affine=affine),
    'sep_conv_5x5': lambda C, stride, affine: SepConv(C, C, 5, stride, 2, affine=affine),
    'sep_conv_7x7': lambda C, stride, affine: SepConv(C, C, 7, stride, 3, affine=affine),
    'dil_conv_3x3': lambda C, stride, affine: DilConv(C, C, 3, stride, 2, 2, affine=affine),
    'dil_conv_5x5': lambda C, stride, affine: DilConv(C, C, 5, stride, 4, 2, affine=affine),
    'group_conv_3x3_2': lambda C, stride, affine: GroupConv(C, C, 3, stride, 1, 2, affine=affine),
    'group_conv_3x3_4': lambda C, stride, affine: GroupConv(C, C, 3, stride, 1, 4, affine=affine),
}


class ReLUConv(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True):
        super(ReLUConv, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_out, kernel_size, stride=stride,
                      padding=padding, bias=False),
        )

    def forward(self, x):
        return self.op(x)


class DilConv(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, dilation, affine=True):
        super(DilConv, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_in, kernel_size=kernel_size, stride=stride,
                      padding=padding, dilation=dilation, groups=C_in, bias=False),
            nn.Conv2d(C_in, C_out, kernel_size=1, padding=0, bias=False),
        )

    def forward(self, x):
        return self.op(x)


class SepConv(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True):
        super(SepConv, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_in, kernel_size=kernel_size,
                      stride=stride, padding=padding, groups=C_in, bias=False),
            nn.Conv2d(C_in, C_in, kernel_size=1, padding=0, bias=False),
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_in, kernel_size=kernel_size, stride=1,
                      padding=padding, groups=C_in, bias=False),
            nn.Conv2d(C_in, C_out, kernel_size=1, padding=0, bias=False),
        )

    def forward(self, x):
        return self.op(x)


class GroupConv(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, groups, affine=True):
        super(GroupConv, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_in, kernel_size=kernel_size,
                      stride=stride, padding=padding, groups=groups, bias=False),
        )

    def forward(self, x):
        return self.op(x)


class Upsampler(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, scale, affine=True):
        super(Upsampler, self).__init__()

        unit = []
        if (scale & (scale - 1)) == 0:
            for _ in range(int(math.log(scale, 2))):
                unit.append(nn.ReLU(inplace=False))
                unit.append(nn.Conv2d(C_in, 4*C_out, kernel_size, stride=stride,
                                   padding=padding, bias=False))
                unit.append(nn.PixelShuffle(2))

        elif scale == 3:
            unit.append(nn.ReLU(inplace=False))
            unit.append(nn.Conv2d(C_in, 9*C_out, kernel_size, stride=stride,
                               padding=padding, bias=False))
            unit.append(nn.PixelShuffle(3))
        else:
            raise NotImplementedError

        self.op = nn.Sequential(*unit)

    def forward(self, x):
        return self.op(x)


class Identity(nn.Module):

    def __init__(self):
        super(Identity, self).__init__()

    def forward(self, x):
        return x


class Zero(nn.Module):

    def __init__(self, stride):
        super(Zero, self).__init__()
        self.stride = stride

    def forward(self, x):
        if self.stride == 1:
            return x.mul(0.)
        return x[:, :, ::self.stride, ::self.stride].mul(0.)


class FactorizedReduce(nn.Module):

    def __init__(self, C_in, C_out, affine=True):
        super(FactorizedReduce, self).__init__()
        assert C_out % 2 == 0
        self.relu = nn.ReLU(inplace=False)
        self.conv_1 = nn.Conv2d(C_in, C_out // 2, 1,
                                stride=2, padding=0, bias=False)
        self.conv_2 = nn.Conv2d(C_in, C_out // 2, 1,
                                stride=2, padding=0, bias=False)

    def forward(self, x):
        x = self.relu(x)

        if self.conv_1(x).size() == self.conv_2(x[:, :, 1:, 1:]).size():
            out = torch.cat(
                [self.conv_1(x), self.conv_2(x[:, :, 1:, 1:])], dim=1)
        else:
            new_conv_2 = F.interpolate(
                self.conv_2(x[:, :, 1:, 1:]),
                size=(self.conv_1(x).size()[2], self.conv_1(x).size()[3]),
                mode="bilinear",
                align_corners=False
            )
            assert self.conv_1(x).size() == new_conv_2.size()
            out = torch.cat([self.conv_1(x), new_conv_2], dim=1)
        return out
