import os
import argparse
from pathlib import Path
import numpy as np
import open3d as o3d
from PIL import Image
from graspnetAPI import GraspGroup

from tracker import AnyGraspTracker

parser = argparse.ArgumentParser()
parser.add_argument('--checkpoint_path', required=True, help='Model checkpoint path')
parser.add_argument('--filter', type=str, default='oneeuro', help='Filter to smooth grasp parameters(rotation, width, depth). [oneeuro/kalman/none]')
parser.add_argument('--debug', action='store_true', help='Enable visualization')
cfgs = parser.parse_args()

TRANS_MAT = np.array([[1,0,0,0],[0,1,0,0],[0,0,-1,0],[0,0,0,1]], dtype=np.float64)


class CameraInfo:
    def __init__(self, width, height, fx, fy, cx, cy, scale):
        self.width = width
        self.height = height
        self.fx = fx
        self.fy = fy
        self.cx = cx
        self.cy = cy
        self.scale = scale


def create_point_cloud_from_depth_image(depth, camera, organized=True):
    assert(depth.shape[0] == camera.height and depth.shape[1] == camera.width)
    xmap = np.arange(camera.width)
    ymap = np.arange(camera.height)
    xmap, ymap = np.meshgrid(xmap, ymap)
    points_z = depth / camera.scale
    points_x = (xmap - camera.cx) * points_z / camera.fx
    points_y = (ymap - camera.cy) * points_z / camera.fy
    points = np.stack([points_x, points_y, points_z], axis=-1)
    if not organized:
        points = points.reshape([-1, 3])
    return points


def get_data(data_dir, index):
    colors = np.array(Image.open(os.path.join(data_dir, 'color_%03d.png'%index)), dtype=np.float32) / 255.0
    depths = np.load(os.path.join(data_dir, 'depth_%03d.npy'%index))

    width, height = depths.shape[1], depths.shape[0]
    fx, fy = 927.17, 927.37
    cx, cy = 651.32, 349.62
    scale = 1000.0
    camera = CameraInfo(width, height, fx, fy, cx, cy, scale)

    points = create_point_cloud_from_depth_image(depths, camera)
    mask = (points[:,:,2] > 0) & (points[:,:,2] < 1.5)
    points = points[mask]
    colors = colors[mask]

    return points, colors


def prepare_frames(data_dir_list, indices):
    anygrasp_tracker = AnyGraspTracker(cfgs)
    anygrasp_tracker.load_net()

    frames = []
    grasp_ids = [0]

    for i in range(len(indices)):
        points, colors = get_data(data_dir_list, indices[i])
        target_gg, curr_gg, target_grasp_ids, corres_preds = anygrasp_tracker.update(points, colors, grasp_ids)

        if i == 0:
            grasp_mask_x = ((curr_gg.translations[:,0]>-0.18) & (curr_gg.translations[:,0]<0.18))
            grasp_mask_y = ((curr_gg.translations[:,1]>-0.12) & (curr_gg.translations[:,1]<0.12))
            grasp_mask_z = ((curr_gg.translations[:,2]>0.35) & (curr_gg.translations[:,2]<0.55))
            grasp_ids = np.where(grasp_mask_x & grasp_mask_y & grasp_mask_z)[0][:30:6]
            target_gg = curr_gg[grasp_ids]
        else:
            grasp_ids = target_grasp_ids

        pts_h = np.hstack([points, np.ones((points.shape[0],1), dtype=np.float64)])
        pts_t = np.dot(pts_h, TRANS_MAT.T)[:, :3]
        cols_t = colors.astype(np.float64)

        grippers = target_gg.to_open3d_geometry_list()
        for g in grippers:
            g.transform(TRANS_MAT)

        frames.append((pts_t, cols_t, grippers))
        print(i, target_grasp_ids, flush=True)

    return frames


def demo(data_dir_list, indices):
    print('Pre-computing frames...', flush=True)
    frames = prepare_frames(data_dir_list, indices)
    print(f'Done. {len(frames)} frames ready.', flush=True)

    frame_idx = [1]  # skip frame 0 (empty grippers), start from frame 1

    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(frames[1][0])
    cloud.colors = o3d.utility.Vector3dVector(frames[1][1])

    gripper_meshes = []
    for gripper_geom in frames[1][2]:
        m = o3d.geometry.TriangleMesh()
        m.vertices = gripper_geom.vertices
        m.triangles = gripper_geom.triangles
        m.compute_vertex_normals()
        gripper_meshes.append(m)

    all_geoms = [cloud] + gripper_meshes

    def callback(vis):
        idx = frame_idx[0]
        if idx >= len(frames):
            return True

        pts, cols, grippers = frames[idx]
        frame_idx[0] = idx + 1

        cloud.points = o3d.utility.Vector3dVector(pts)
        cloud.colors = o3d.utility.Vector3dVector(cols)
        vis.update_geometry(cloud)

        while len(gripper_meshes) < len(grippers):
            empty = o3d.geometry.TriangleMesh()
            empty.vertices = o3d.utility.Vector3dVector()
            empty.triangles = o3d.utility.Vector3iVector()
            gripper_meshes.append(empty)
            vis.add_geometry(empty)

        for k, m in enumerate(gripper_meshes):
            if k < len(grippers):
                m.vertices = grippers[k].vertices
                m.triangles = grippers[k].triangles
                m.compute_vertex_normals()
                vis.update_geometry(m)

        vis.poll_events()
        vis.update_renderer()
        return False

    all_geoms = [cloud] + gripper_meshes
    o3d.visualization.draw_geometries_with_animation_callback(
        all_geoms,
        callback,
        window_name='AnyGrasp Tracking',
        width=1280,
        height=720,
    )


if __name__ == "__main__":
    script_dir = Path(__file__).parent.resolve()
    data_dir = script_dir / "example_data"
    demo(str(data_dir), list(range(30)))
