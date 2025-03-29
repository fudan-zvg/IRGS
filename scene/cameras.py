#
# Copyright (C) 2023, Inria
# GRAPHDECO research group, https://team.inria.fr/graphdeco
# All rights reserved.
#
# This software is free for non-commercial, research and evaluation use 
# under the terms of the LICENSE.md file.
#
# For inquiries contact  george.drettakis@inria.fr
#

import torch
from torch import nn
import torch.nn.functional as F
import numpy as np
from utils.graphics_utils import getWorld2View2, getProjectionMatrix, getProjectionMatrixCorrect

class Camera(nn.Module):
    def __init__(self, colmap_id, R, T, FoVx, FoVy, image, gt_alpha_mask,
                 image_name, uid,
                 trans=np.array([0.0, 0.0, 0.0]), scale=1.0, 
                 data_device = "cuda", HWK = None, mask = None, image_path=None,
                 ):
        super(Camera, self).__init__()

        self.uid = uid
        self.colmap_id = colmap_id
        self.R = R
        self.T = T
        self.FoVx = FoVx
        self.FoVy = FoVy
        self.image_name = image_name
        self.image_path = image_path

        try:
            self.data_device = torch.device(data_device)
        except Exception as e:
            print(e)
            print(f"[Warning] Custom device {data_device} failed, fallback to default cuda device" )
            self.data_device = torch.device("cuda")
            
        if mask is not None:
            self.mask = mask.to(self.data_device)
        else:
            self.mask = None
            
        if image is not None:
            self.original_image = image.clamp(0.0, 1.0).to(self.data_device)
            self.image_width = self.original_image.shape[2]
            self.image_height = self.original_image.shape[1]

            if gt_alpha_mask is not None:
                self.original_image *= gt_alpha_mask.to(self.data_device)
                self.gt_alpha_mask = gt_alpha_mask.to(self.data_device)
            else:
                self.original_image *= torch.ones((1, self.image_height, self.image_width), device=self.data_device)
                self.gt_alpha_mask = None

        if HWK is not None:
            assert self.image_width == int(HWK[1])
            assert self.image_height == int(HWK[0])

        self.zfar = 100.0
        self.znear = 0.01

        self.trans = trans
        self.scale = scale
        self.world_view_transform = torch.tensor(getWorld2View2(R, T, trans, scale)).transpose(0, 1).cuda()
        if HWK is None:
            focal = self.image_width / (2 * np.tan(self.FoVx * 0.5))
            K = np.array([
                [focal, 0, self.image_width/2],
                [0, focal, self.image_height/2],
                [0, 0, 1],
            ])
            self.HWK = (self.image_height,  self.image_width, K)
            self.projection_matrix = getProjectionMatrix(znear=self.znear, zfar=self.zfar, fovX=self.FoVx, fovY=self.FoVy).transpose(0,1).cuda()
        else:
            self.HWK = HWK
            self.projection_matrix = getProjectionMatrixCorrect(self.znear, self.zfar, HWK[0], HWK[1], HWK[2]).transpose(0,1).cuda()
        self.full_proj_transform = (self.world_view_transform.unsqueeze(0).bmm(self.projection_matrix.unsqueeze(0))).squeeze(0)
        self.camera_center = self.world_view_transform.inverse()[3, :3]
        self.R = torch.tensor(self.R, dtype=torch.float32, device='cuda')
        self.T = torch.tensor(self.T, dtype=torch.float32, device='cuda')
        self.c2w = self.world_view_transform.transpose(0, 1).inverse()
        
        v, u = torch.meshgrid(torch.arange(self.image_height, device='cuda'),
                              torch.arange(self.image_width, device='cuda'), indexing="ij")
        focal_x = self.image_width / (2 * np.tan(self.FoVx * 0.5))
        focal_y = self.image_height / (2 * np.tan(self.FoVy * 0.5))
        rays_d_camera = torch.stack([(u - self.image_width / 2 + 0.5) / focal_x,
                                  (v - self.image_height / 2 + 0.5) / focal_y,
                                  torch.ones_like(u)], dim=-1).reshape(-1, 3)
        rays_d = rays_d_camera @ self.world_view_transform[:3, :3].T
        self.rays_d_unnormalized = rays_d
        self.rays_d = F.normalize(rays_d, dim=-1)
        self.rays_o = self.camera_center[None].expand_as(self.rays_d)
        self.rays_rgb = self.original_image.permute(1, 2, 0).reshape(-1, 3)
        self.rays_d_hw = self.rays_d.reshape(self.image_height, self.image_width, 3)
        self.rays_d_hw_unnormalized = rays_d.reshape(self.image_height, self.image_width, 3)

    def get_rays(self):
        return self.rays_o, self.rays_d
        
    def get_rays_rgb(self):
        return self.original_image.permute(1, 2, 0).reshape(-1, 3)
        
    def get_intrinsics(self):
        focal_x = self.image_width / (2 * np.tan(self.FoVx * 0.5))
        focal_y = self.image_height / (2 * np.tan(self.FoVy * 0.5))

        return torch.tensor([[focal_x, 0, self.image_width / 2],
                                [0, focal_y, self.image_height / 2],
                                [0, 0, 1]], device='cuda', dtype=torch.float32)

class MiniCam:
    def __init__(self, width, height, fovy, fovx, znear, zfar, world_view_transform, full_proj_transform):
        self.image_width = width
        self.image_height = height    
        self.FoVy = fovy
        self.FoVx = fovx
        self.znear = znear
        self.zfar = zfar
        self.world_view_transform = world_view_transform
        self.full_proj_transform = full_proj_transform
        view_inv = torch.inverse(self.world_view_transform)
        self.camera_center = view_inv[3][:3]

