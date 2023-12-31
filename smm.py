# https://raw.githubusercontent.com/pytorch/examples/master/dcgan/main.py

from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
import torchvision.datasets as dset
import torchvision.transforms as transforms
import torchvision.utils as vutils
from Model import UNet
import torch.nn.functional as F
import sys
import logging


modelConfig = {
    "state": "train", # or eval
    "epoch": 200,
    "batch_size": 80,
    "T": 20,
    "channel": 32,
    "channel_mult": [1, 2, 3, 4],
    "attn": [2],
    "num_res_blocks": 1,
    "dropout": 0.15,
    "lr": 1e-4,
    "multiplier": 2.,
    "beta_1": 1e-4,
    "beta_T": 0.02,
    "img_size": 32,
    "grad_clip": 1.,
    "device": "cuda:0", ### MAKE SURE YOU HAVE A GPU !!!
    "training_load_weight": "DiffusionWeight.pt",
    "save_weight_dir": "./Checkpoints/",
    "test_load_weight": "ckpt_199_.pt",
    "sampled_dir": "./SampledImgs/",
    "sampledNoisyImgName": "NoisyNoGuidenceImgs.png",
    "sampledImgName": "SampledNoGuidenceImgs.png",
    "nrow": 8
    } 
 
#net_model.load_state_dict(torch.load(os.path.join(
    #modelConfig["save_weight_dir"], modelConfig["training_load_weight"]), map_location=device)) 
# python smm.py --dataset cifar10 --dataroot ./data/cifar10 --imageSize 32 --cuda --outf out_cifar --manualSeed 13 --niter 100

class Generator(nn.Module):
    def __init__(self, ngpu, nc=3, nz=100, ngf=64):
        super(Generator, self).__init__()
        self.ngpu = ngpu
        self.main = nn.Sequential(
            # input is Z, going into a convolution
            nn.ConvTranspose2d(     nz, ngf * 8, 4, 1, 0, bias=False),
            nn.BatchNorm2d(ngf * 8),
            nn.ReLU(True),
            # state size. (ngf*8) x 4 x 4
            nn.ConvTranspose2d(ngf * 8, ngf * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ngf * 4),
            nn.ReLU(True),
            # state size. (ngf*4) x 8 x 8
            nn.ConvTranspose2d(ngf * 4, ngf * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ngf * 2),
            nn.ReLU(True),
            # state size. (ngf*2) x 16 x 16
            nn.ConvTranspose2d(ngf * 2,     ngf, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ngf),
            nn.ReLU(True),
            nn.ConvTranspose2d(    ngf,      nc, kernel_size=1, stride=1, padding=0, bias=False),
            nn.Tanh()
        )

    def forward(self, input):
        if input.is_cuda and self.ngpu > 1:
            output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)
        return output


class Discriminator(nn.Module):
    def __init__(self, ngpu, nc=3, ndf=64):
        super(Discriminator, self).__init__()
        self.ngpu = ngpu
        self.main = nn.Sequential(
            # input is (nc) x 64 x 64
            nn.Conv2d(nc, ndf, 4, 2, 1, bias=False),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf) x 32 x 32
            nn.Conv2d(ndf, ndf * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ndf * 2),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf*2) x 16 x 16
            nn.Conv2d(ndf * 2, ndf * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ndf * 4),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf*4) x 8 x 8
            nn.Conv2d(ndf * 4, ndf * 8, 4, 2, 1, bias=False),
            nn.BatchNorm2d(ndf * 8),
            nn.LeakyReLU(0.2, inplace=True),
            # state size. (ndf*8) x 4 x 4
            nn.Conv2d(ndf * 8, 1, 2, 2, 0, bias=False),
            nn.Sigmoid()
        )

    def forward(self, input):
        if input.is_cuda and self.ngpu > 1:
            output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)

        return output.view(-1, 1).squeeze(1)
    
def extract(v, t, x_shape):
    """
    Extract some coefficients at specified timesteps, then reshape to
    [batch_size, 1, 1, 1, 1, ...] for broadcasting purposes.
    """
    device = t.device
    out = torch.gather(v, index=t, dim=0).float().to(device)
    return out.view([t.shape[0]] + [1] * (len(x_shape) - 1))
T = modelConfig['T']


betas =  torch.linspace(modelConfig['beta_1'], modelConfig['beta_T'], T).double()
alphas = 1. - betas
alphas_bar = torch.cumprod(alphas, dim=0)
sqrt_alphas_bar = torch.sqrt(alphas_bar).cuda()
sqrt_one_minus_alphas_bar =  torch.sqrt(1. - alphas_bar).cuda()
# calculations for diffusion q(x_t | x_{t-1}) and others

if __name__ == '__main__':    

    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True, help='cifar10 | lsun | mnist |imagenet | folder | lfw | fake')
    parser.add_argument('--dataroot', required=True, help='path to dataset')
    parser.add_argument('--workers', type=int, help='number of data loading workers', default=2)
    parser.add_argument('--batchSize', type=int, default=64, help='input batch size')
    parser.add_argument('--imageSize', type=int, default=64, help='the height / width of the input image to network')
    parser.add_argument('--nz', type=int, default=100, help='size of the latent z vector')
    parser.add_argument('--ngf', type=int, default=64)
    parser.add_argument('--ndf', type=int, default=64)
    parser.add_argument('--niter', type=int, default=25, help='number of epochs to train for')
    parser.add_argument('--lr', type=float, default=0.0002, help='learning rate, default=0.0002')
    parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
    parser.add_argument('--cuda', action='store_true', help='enables cuda')
    parser.add_argument('--ngpu', type=int, default=1, help='number of GPUs to use')
    parser.add_argument('--netG', default='', help="path to netG (to continue training)")
    parser.add_argument('--netS', default='', help="path to netS (to continue training)")
    parser.add_argument('--outf', default='.', help='folder to output images and model checkpoints')
    parser.add_argument('--manualSeed', type=int, help='manual seed')

    opt = parser.parse_args()
    print(opt)
    
    try:
        os.makedirs(opt.outf)
    except OSError:
        pass

    if opt.manualSeed is None:
        opt.manualSeed = random.randint(1, 10000)
    print("Random Seed: ", opt.manualSeed)
    random.seed(opt.manualSeed)
    torch.manual_seed(opt.manualSeed)

    cudnn.benchmark = True

    if torch.cuda.is_available() and not opt.cuda:
        print("WARNING: You have a CUDA device, so you should probably run with --cuda")


    dataset = dset.CIFAR10(root=opt.dataroot, download=True,
                           transform=transforms.Compose([
                               transforms.Resize(opt.imageSize),
                               transforms.ToTensor(),
                               transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
                           ]))
    nc=3

    assert dataset
    dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize,
                                             shuffle=True, num_workers=int(opt.workers))

    device = torch.device("cuda:0" if opt.cuda else "cpu")
    ngpu = int(opt.ngpu)
    nz = int(opt.nz)
    ngf = int(opt.ngf)
    ndf = int(opt.ndf)


    # custom weights initialization called on netG and netS
    def weights_init(m):
        classname = m.__class__.__name__
        if classname.find('Conv') != -1:
            m.weight.data.normal_(0.0, 0.02)
        elif classname.find('BatchNorm') != -1:
            m.weight.data.normal_(1.0, 0.02)
            m.bias.data.fill_(0)

    netG = Generator(ngpu).to(device)
    netG.apply(weights_init)
    netS=   UNet(T=modelConfig["T"], ch=modelConfig["channel"], ch_mult=modelConfig["channel_mult"], attn=modelConfig["attn"],
                     num_res_blocks=modelConfig["num_res_blocks"], dropout=modelConfig["dropout"]).to(device)
    netS.train()
    netS.requires_grad_=True
    if opt.netG != '':
        netG.load_state_dict(torch.load(opt.netG))
    print(netG)



    netS = netS# Discriminator(ngpu).to(device)
    #netS.apply(weights_init)
    if opt.netS != '':
        netS.load_state_dict(torch.load(opt.netS))
    print(netS)

    criterion = nn.BCELoss()

    fixed_noise = torch.randn(opt.batchSize, nz, 1, 1, device=device)
    real_label = 1
    fake_label = 0

    # setup optimizer
    optimizerS = optim.Adam(netS.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
    optimizerG = optim.Adam(netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))

    for epoch in range(opt.niter):
        for i, data in enumerate(dataloader, 0):
            ############################
            # (1) Update score network:
            ###########################
            # train with real
            net.zero_grad()
            real_cpu = data[0].to(device)
            batch_size = real_cpu.size(0)

            #noise = torch.randn(batch_size, nz, 1, 1, device=device)
            
            x_0 = real_cpu#netG(noise)
             #fake
            t = torch.randint(T, size=(x_0.shape[0], ), device=x_0.device)
            noise = torch.randn_like(x_0)
            x_t = (
                x_0 +
                extract(sqrt_one_minus_alphas_bar, t, x_0.shape) * noise)
            loss = F.mse_loss(netS(x_t, t), noise, reduction='none')    
            D_x = loss.mean().item()
            errD_real = loss.sum() / 1000
            errD_real.backward()
            
            
            # train with fake


            noise = torch.randn(batch_size, nz, 1, 1, device=device)
            #fake = netG(noise)
            
            x_0 = netG(noise)
             #fake
            t = torch.randint(T, size=(x_0.shape[0], ), device=x_0.device)
            noise = torch.randn_like(x_0)
            noise2 = torch.randn_like(x_0)
            x_t = (
                x_0 +
                extract(sqrt_one_minus_alphas_bar, t, x_0.shape) * noise)
            loss = F.mse_loss(netS(x_t, t), noise2, reduction='none')   
            D_G_z1 = loss.mean().item()
            
            errD_fake = loss.sum() / 1000
            #errG =errG+loss
            errD_fake.backward()
            errD = errD_real + errD_fake
            optimizerS.step()
            
            ############################
            # (2) Update G network:
            ###########################
            #netG.zero_grad()
       
            netG.zero_grad()
            
            noise = torch.randn(batch_size, nz, 1, 1, device=device)
            
            x_0 =netG(noise)
             #fake
            t = torch.randint(T, size=(x_0.shape[0], ), device=x_0.device)
            noise = torch.randn_like(x_0)
            x_t = (
                 x_0 +
                extract(sqrt_one_minus_alphas_bar, t, x_0.shape) * noise)
            loss = F.mse_loss(netS(x_t, t), noise, reduction='none')  
            D_G_z2 = loss.mean().item()
            
            errG = loss.sum() / 1000
            #errG =errG+loss
            errG.backward()
            optimizerG.step()

            print('[%d/%d][%d/%d] Loss_D: %.4f Loss_G: %.4f D(x): %.4f D(G(z)): %.4f / %.4f'
                  % (epoch, opt.niter, i, len(dataloader),
                     errD.item(), errG.item(), D_x, D_G_z1, D_G_z2))
            if i % 100 == 0:
                vutils.save_image(real_cpu,
                        '%s/real_samples.png' % opt.outf,
                        normalize=True)
                fake = netG(fixed_noise)
                vutils.save_image(fake.detach(),
                        '%s/fake_samples_epoch_%03d.png' % (opt.outf, epoch),
                        normalize=True)

        # do checkpointing
        torch.save(netG.state_dict(), '%s/netG_epoch_%d.pth' % (opt.outf, epoch))
        torch.save(netS.state_dict(), '%s/netS_epoch_%d.pth' % (opt.outf, epoch))
