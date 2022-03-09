# -*- coding: utf-8 -*-
#
from cmath import isnan
import logging
import os
import traceback
import numpy as np
import itertools
import math
import copy
import bezier
import csv
import random
import string
from collections import Counter

from module.MOptions import MExportOptions
from mmd.PmxData import (
    PmxModel,
    Vertex,
    Material,
    Bone,
    Morph,
    DisplaySlot,
    RigidBody,
    Joint,
    Bdef1,
    Bdef2,
    Bdef4,
    Sdef,
    RigidBodyParam,
    IkLink,
    Ik,
    BoneMorphData,
)
from mmd.PmxWriter import PmxWriter
from module.MMath import MVector2D, MVector3D, MVector4D, MQuaternion, MMatrix4x4
from utils.MLogger import MLogger
from utils.MException import SizingException, MKilledException
import utils.MBezierUtils as MBezierUtils

logger = MLogger(__name__, level=1)


class VirtualVertex:
    def __init__(self, key):
        self.key = key
        # 実頂点
        self.real_vertices = []
        # 実頂点の位置リスト
        self.positions = []
        # 実頂点の法線リスト
        self.normals = []
        # 実頂点の面リスト（処理対象）
        self.indexes = []
        # 実頂点の面リスト（処理対象外）
        self.out_indexes = []
        # 実頂点の遷移先仮想頂点リスト
        self.connected_vvs = []
        # 対象頂点に対するウェイト情報
        self.deform = None
        # 対象頂点に対するボーン情報
        self.bone = None
        self.parent_bone = None
        self.bone_regist = False
        # 対象頂点に対する剛体情報
        self.rigidbody = None
        self.rigidbody_qq = None
        self.balance_rigidbody = None
        self.balance_rigidbody_qq = None
        # 対象頂点に対するジョイント情報
        self.vertical_joint = None
        self.vertical_balancer_joint = None
        self.horizonal_joint = None
        self.diagonal_joint = None
        self.reverse_joint = None

    def append(self, real_vertices: list, connected_vvs: list, indexes: list):
        for rv in real_vertices:
            if rv not in self.real_vertices:
                self.real_vertices.append(rv)
                self.positions.append(rv.position.data())
                self.normals.append(rv.normal.data())

        for lv in connected_vvs:
            if lv not in self.connected_vvs:
                self.connected_vvs.append(lv)

        for i in indexes:
            if i not in self.indexes:
                self.indexes.append(i)

    def vidxs(self):
        return [v.index for v in self.real_vertices]

    def position(self):
        if not self.positions:
            return MVector3D()
        return MVector3D(np.mean(self.positions, axis=0))

    def normal(self):
        if not self.normals:
            return MVector3D()
        return MVector3D(np.mean(self.normals, axis=0))

    def __str__(self):
        return f"v[{','.join([str(v.index) for v in self.real_vertices])}] pos[{self.position().to_log()}] nor[{self.normal().to_log()}], lines[{self.connected_vvs}], indexes[{self.indexes}], out_indexes[{self.out_indexes}]"


class PmxTailorExportService:
    def __init__(self, options: MExportOptions):
        self.options = options

    def execute(self):
        logging.basicConfig(level=self.options.logging_level, format="%(message)s [%(module_name)s]")

        try:
            service_data_txt = f"{logger.transtext('PmxTailor変換処理実行')}\n------------------------\n{logger.transtext('exeバージョン')}: {self.options.version_name}\n"
            service_data_txt = (
                f"{service_data_txt}　{logger.transtext('元モデル')}: {os.path.basename(self.options.pmx_model.path)}\n"
            )

            for pidx, param_option in enumerate(self.options.param_options):
                service_data_txt = f"{service_data_txt}\n　【No.{pidx + 1}】 --------- "
                service_data_txt = f"{service_data_txt}\n　　{logger.transtext('材質')}: {param_option['material_name']}"
                service_data_txt = f"{service_data_txt}\n　　{logger.transtext('剛体グループ')}: {param_option['rigidbody'].collision_group + 1}"
                service_data_txt = f"{service_data_txt}\n　　{logger.transtext('密集度')}: {param_option['threshold']}"
                service_data_txt = f"{service_data_txt}\n　　{logger.transtext('細かさ')}: {param_option['fineness']}"
                service_data_txt = f"{service_data_txt}\n　　{logger.transtext('質量')}: {param_option['mass']}"
                service_data_txt = (
                    f"{service_data_txt}\n　　{logger.transtext('柔らかさ')}: {param_option['air_resistance']}"
                )
                service_data_txt = (
                    f"{service_data_txt}\n　　{logger.transtext('張り')}: {param_option['shape_maintenance']}"
                )

            logger.info(service_data_txt, translate=False, decoration=MLogger.DECORATION_BOX)

            model = self.options.pmx_model
            model.comment += f"\r\n\r\n{logger.transtext('物理')}: PmxTailor"

            # 保持ボーンは全設定を確認する
            saved_bone_names = self.get_saved_bone_names(model)

            for pidx, param_option in enumerate(self.options.param_options):
                if not self.create_physics(model, param_option, saved_bone_names):
                    return False

            # 最後に出力
            logger.info("PMX出力開始", decoration=MLogger.DECORATION_LINE)

            PmxWriter().write(model, self.options.output_path)

            logger.info(
                "出力終了: %s",
                os.path.basename(self.options.output_path),
                decoration=MLogger.DECORATION_BOX,
                title=logger.transtext("成功"),
            )

            return True
        except MKilledException:
            return False
        except SizingException as se:
            logger.error("PmxTailor変換処理が処理できないデータで終了しました。\n\n%s", se.message, decoration=MLogger.DECORATION_BOX)
        except Exception:
            logger.critical(
                "PmxTailor変換処理が意図せぬエラーで終了しました。\n\n%s", traceback.format_exc(), decoration=MLogger.DECORATION_BOX
            )
        finally:
            logging.shutdown()

    def get_saved_bone_names(self, model: PmxModel):
        # TODO
        return []

    def create_physics(self, model: PmxModel, param_option: dict, saved_bone_names: list):
        model.comment += f"\r\n{logger.transtext('材質')}: {param_option['material_name']} --------------"
        model.comment += f"\r\n　　{logger.transtext('剛体グループ')}: {param_option['rigidbody'].collision_group + 1}"
        model.comment += f", {logger.transtext('細かさ')}: {param_option['fineness']}"
        model.comment += f", {logger.transtext('質量')}: {param_option['mass']}"
        model.comment += f", {logger.transtext('柔らかさ')}: {param_option['air_resistance']}"
        model.comment += f", {logger.transtext('張り')}: {param_option['shape_maintenance']}"

        material_name = param_option["material_name"]

        # 頂点CSVが指定されている場合、対象頂点リスト生成
        if param_option["vertices_csv"]:
            target_vertices = []
            try:
                with open(param_option["vertices_csv"], encoding="cp932", mode="r") as f:
                    reader = csv.reader(f)
                    next(reader)  # ヘッダーを読み飛ばす
                    for row in reader:
                        if len(row) > 1 and int(row[1]) in model.material_vertices[material_name]:
                            target_vertices.append(int(row[1]))
            except Exception:
                logger.warning("頂点CSVが正常に読み込めなかったため、処理を終了します", decoration=MLogger.DECORATION_BOX)
                return None, None
        else:
            target_vertices = list(model.material_vertices[material_name])

        if param_option["exist_physics_clear"] == logger.transtext("再利用"):
            # TODO
            pass
        else:
            logger.info("【%s】頂点マップ生成", material_name, decoration=MLogger.DECORATION_LINE)

            vertex_maps, virtual_vertices, remaining_vertices, back_vertices = self.create_vertex_map(
                model, param_option, material_name, target_vertices
            )

            if not vertex_maps:
                return False

            # 各頂点の有効INDEX数が最も多いものをベースとする
            map_cnt = []
            for vertex_map in vertex_maps:
                map_cnt.append(np.count_nonzero(~np.isnan(vertex_map)) / 3)

            if len(map_cnt) == 0:
                logger.warning("有効な頂点マップが生成できなかった為、処理を終了します", decoration=MLogger.DECORATION_BOX)
                return False

            vertex_map_orders = [k for k in np.argsort(-np.array(map_cnt)) if map_cnt[k] > np.max(map_cnt) * 0.5]

            (
                root_bone,
                virtual_vertices,
                all_regist_bones,
                all_bone_vertical_distances,
                all_bone_horizonal_distances,
                all_bone_connected,
            ) = self.create_bone(model, param_option, material_name, virtual_vertices, vertex_maps, vertex_map_orders)

            self.create_weight(
                model,
                param_option,
                material_name,
                virtual_vertices,
                vertex_maps,
                all_regist_bones,
                all_bone_vertical_distances,
                all_bone_horizonal_distances,
                all_bone_connected,
            )

            # 残ウェイト
            self.create_remaining_weight(model, param_option, material_name, virtual_vertices, remaining_vertices)

            # 裏ウェイト
            self.create_back_weight(model, param_option, material_name, virtual_vertices, back_vertices)

        root_rigidbody = self.create_rigidbody(
            model,
            param_option,
            material_name,
            virtual_vertices,
            vertex_maps,
            all_regist_bones,
            all_bone_connected,
            root_bone,
        )

        self.create_joint(
            model,
            param_option,
            material_name,
            virtual_vertices,
            vertex_maps,
            all_regist_bones,
            all_bone_connected,
            root_rigidbody,
        )

        return True

    def create_joint(
        self,
        model: PmxModel,
        param_option: dict,
        material_name: str,
        virtual_vertices: dict,
        vertex_maps: dict,
        all_regist_bones: dict,
        all_bone_connected: dict,
        root_rigidbody: RigidBody,
    ):
        logger.info("【%s】ジョイント生成", material_name, decoration=MLogger.DECORATION_LINE)

        # ジョイント生成
        created_joints = {}
        prev_joint_cnt = 0

        for base_map_idx, regist_bones in all_regist_bones.items():
            logger.info("--【No.%s】ジョイント生成", base_map_idx + 1)

            vertex_map = vertex_maps[base_map_idx]
            bone_connected = all_bone_connected[base_map_idx]

            # 上下はY軸比較, 左右はX軸比較
            target_idx = 1 if param_option["direction"] in ["上", "下"] else 0
            target_direction = 1 if param_option["direction"] in ["上", "右"] else -1

            # キーは比較対象＋向きで昇順
            vv_keys = sorted(np.unique(vertex_map[np.where(regist_bones)][:, target_idx]) * target_direction)

            # 縦ジョイント情報
            (
                vertical_limit_min_mov_xs,
                vertical_limit_min_mov_ys,
                vertical_limit_min_mov_zs,
                vertical_limit_max_mov_xs,
                vertical_limit_max_mov_ys,
                vertical_limit_max_mov_zs,
                vertical_limit_min_rot_xs,
                vertical_limit_min_rot_ys,
                vertical_limit_min_rot_zs,
                vertical_limit_max_rot_xs,
                vertical_limit_max_rot_ys,
                vertical_limit_max_rot_zs,
                vertical_spring_constant_mov_xs,
                vertical_spring_constant_mov_ys,
                vertical_spring_constant_mov_zs,
                vertical_spring_constant_rot_xs,
                vertical_spring_constant_rot_ys,
                vertical_spring_constant_rot_zs,
            ) = self.create_joint_param(
                param_option["vertical_joint"], vv_keys, param_option["vertical_joint_coefficient"]
            )

            # 横ジョイント情報
            (
                horizonal_limit_min_mov_xs,
                horizonal_limit_min_mov_ys,
                horizonal_limit_min_mov_zs,
                horizonal_limit_max_mov_xs,
                horizonal_limit_max_mov_ys,
                horizonal_limit_max_mov_zs,
                horizonal_limit_min_rot_xs,
                horizonal_limit_min_rot_ys,
                horizonal_limit_min_rot_zs,
                horizonal_limit_max_rot_xs,
                horizonal_limit_max_rot_ys,
                horizonal_limit_max_rot_zs,
                horizonal_spring_constant_mov_xs,
                horizonal_spring_constant_mov_ys,
                horizonal_spring_constant_mov_zs,
                horizonal_spring_constant_rot_xs,
                horizonal_spring_constant_rot_ys,
                horizonal_spring_constant_rot_zs,
            ) = self.create_joint_param(
                param_option["horizonal_joint"], vv_keys, param_option["horizonal_joint_coefficient"]
            )

            # 斜めジョイント情報
            (
                diagonal_limit_min_mov_xs,
                diagonal_limit_min_mov_ys,
                diagonal_limit_min_mov_zs,
                diagonal_limit_max_mov_xs,
                diagonal_limit_max_mov_ys,
                diagonal_limit_max_mov_zs,
                diagonal_limit_min_rot_xs,
                diagonal_limit_min_rot_ys,
                diagonal_limit_min_rot_zs,
                diagonal_limit_max_rot_xs,
                diagonal_limit_max_rot_ys,
                diagonal_limit_max_rot_zs,
                diagonal_spring_constant_mov_xs,
                diagonal_spring_constant_mov_ys,
                diagonal_spring_constant_mov_zs,
                diagonal_spring_constant_rot_xs,
                diagonal_spring_constant_rot_ys,
                diagonal_spring_constant_rot_zs,
            ) = self.create_joint_param(
                param_option["diagonal_joint"], vv_keys, param_option["diagonal_joint_coefficient"]
            )

            # 逆ジョイント情報
            (
                reverse_limit_min_mov_xs,
                reverse_limit_min_mov_ys,
                reverse_limit_min_mov_zs,
                reverse_limit_max_mov_xs,
                reverse_limit_max_mov_ys,
                reverse_limit_max_mov_zs,
                reverse_limit_min_rot_xs,
                reverse_limit_min_rot_ys,
                reverse_limit_min_rot_zs,
                reverse_limit_max_rot_xs,
                reverse_limit_max_rot_ys,
                reverse_limit_max_rot_zs,
                reverse_spring_constant_mov_xs,
                reverse_spring_constant_mov_ys,
                reverse_spring_constant_mov_zs,
                reverse_spring_constant_rot_xs,
                reverse_spring_constant_rot_ys,
                reverse_spring_constant_rot_zs,
            ) = self.create_joint_param(
                param_option["reverse_joint"], vv_keys, param_option["reverse_joint_coefficient"]
            )

            for v_yidx, v_xidx in zip(np.where(regist_bones)[0], np.where(regist_bones)[1]):
                bone_key = tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))
                vv = virtual_vertices[bone_key]
                bone_y_idx = np.where(vv_keys == bone_key[target_idx] * target_direction)[0]
                bone_y_idx = bone_y_idx[0] if bone_y_idx else 0

                prev_xidx, next_xidx, above_yidx, below_yidx = self.get_block_vidxs(
                    v_yidx, v_xidx, regist_bones, bone_connected
                )

                now_above_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[above_yidx, v_xidx]))]
                prev_above_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[above_yidx, prev_xidx]))]
                next_above_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[above_yidx, next_xidx]))]
                now_prev_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, prev_xidx]))]
                now_next_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, next_xidx]))]
                now_below_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[below_yidx, v_xidx]))]
                prev_below_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[below_yidx, prev_xidx]))]
                next_below_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[below_yidx, next_xidx]))]

                if param_option["vertical_joint"]:
                    if v_yidx == 0:
                        joint_pos = vv.bone.position
                        a_rigidbody = root_rigidbody
                        b_rigidbody = vv.rigidbody

                        # 剛体進行方向(x) 中心剛体との角度は反映させない
                        tail_vv = now_below_vv
                        x_direction_pos = (vv.bone.position - tail_vv.bone.position).normalized()
                    else:
                        joint_pos = vv.bone.position
                        a_rigidbody = now_above_vv.rigidbody
                        b_rigidbody = vv.rigidbody

                        # 剛体進行方向(x)
                        tail_vv = now_above_vv
                        x_direction_pos = (tail_vv.bone.position - vv.bone.position).normalized()

                    if not a_rigidbody or not b_rigidbody or (a_rigidbody == b_rigidbody):
                        continue

                    # 剛体進行方向に対しての縦軸(y)
                    y_direction_pos = (
                        (vv.normal().normalized() + tail_vv.normal().normalized()) / 2
                    ).normalized() * -1
                    joint_qq = MQuaternion.fromDirection(y_direction_pos, x_direction_pos)

                    joint_key, joint = self.build_joint(
                        "↓",
                        0,
                        bone_y_idx,
                        a_rigidbody,
                        b_rigidbody,
                        joint_pos,
                        joint_qq,
                        vertical_limit_min_mov_xs,
                        vertical_limit_min_mov_ys,
                        vertical_limit_min_mov_zs,
                        vertical_limit_max_mov_xs,
                        vertical_limit_max_mov_ys,
                        vertical_limit_max_mov_zs,
                        vertical_limit_min_rot_xs,
                        vertical_limit_min_rot_ys,
                        vertical_limit_min_rot_zs,
                        vertical_limit_max_rot_xs,
                        vertical_limit_max_rot_ys,
                        vertical_limit_max_rot_zs,
                        vertical_spring_constant_mov_xs,
                        vertical_spring_constant_mov_ys,
                        vertical_spring_constant_mov_zs,
                        vertical_spring_constant_rot_xs,
                        vertical_spring_constant_rot_ys,
                        vertical_spring_constant_rot_zs,
                    )
                    vv.vertical_joint = joint
                    created_joints[joint_key] = joint

                    # TODO バランサー剛体

                if param_option["horizonal_joint"]:
                    a_rigidbody = vv.rigidbody
                    b_rigidbody = now_next_vv.rigidbody

                    if not a_rigidbody or not b_rigidbody or (a_rigidbody == b_rigidbody):
                        continue

                    # 剛体が重なる箇所の交点
                    now_mat = MMatrix4x4()
                    now_mat.setToIdentity()
                    now_mat.translate(vv.rigidbody.shape_position)
                    now_mat.rotate(vv.rigidbody_qq)
                    now_point = now_mat * MVector3D(vv.rigidbody.shape_size.x(), 0, 0)

                    next_mat = MMatrix4x4()
                    next_mat.setToIdentity()
                    next_mat.translate(now_next_vv.rigidbody.shape_position)
                    next_mat.rotate(now_next_vv.rigidbody_qq)
                    now_next_point = next_mat * MVector3D(-now_next_vv.rigidbody.shape_size.x(), 0, 0)

                    joint_pos = (now_point + now_next_point) / 2
                    joint_qq = MQuaternion.slerp(now_next_vv.rigidbody_qq, vv.rigidbody_qq, 0.5)

                    joint_key, joint = self.build_joint(
                        "→",
                        1,
                        bone_y_idx,
                        a_rigidbody,
                        b_rigidbody,
                        joint_pos,
                        joint_qq,
                        horizonal_limit_min_mov_xs,
                        horizonal_limit_min_mov_ys,
                        horizonal_limit_min_mov_zs,
                        horizonal_limit_max_mov_xs,
                        horizonal_limit_max_mov_ys,
                        horizonal_limit_max_mov_zs,
                        horizonal_limit_min_rot_xs,
                        horizonal_limit_min_rot_ys,
                        horizonal_limit_min_rot_zs,
                        horizonal_limit_max_rot_xs,
                        horizonal_limit_max_rot_ys,
                        horizonal_limit_max_rot_zs,
                        horizonal_spring_constant_mov_xs,
                        horizonal_spring_constant_mov_ys,
                        horizonal_spring_constant_mov_zs,
                        horizonal_spring_constant_rot_xs,
                        horizonal_spring_constant_rot_ys,
                        horizonal_spring_constant_rot_zs,
                    )
                    vv.horizonal_joint = joint
                    created_joints[joint_key] = joint

                if len(created_joints) > 0 and len(created_joints) // 100 > prev_joint_cnt:
                    logger.info("-- -- 【No.%s】ジョイント: %s個目:終了", base_map_idx + 1, len(created_joints))
                    prev_joint_cnt = len(created_joints) // 100

        logger.info("-- ジョイント: %s個目:終了", len(created_joints))

        for joint_key in sorted(created_joints.keys()):
            # ジョイントを登録
            joint = created_joints[joint_key]
            joint.index = len(model.joints)

            if joint.name in model.joints:
                logger.warning("同じジョイント名が既に登録されているため、末尾に乱数を追加します。 既存ジョイント名: %s", joint.name)
                joint.name += randomname(3)

            model.joints[joint.name] = joint
            logger.debug(f"joint: {joint}")

    def build_joint(
        self,
        direction_mark: str,
        direction_idx: int,
        bone_y_idx: int,
        a_rigidbody: RigidBody,
        b_rigidbody: RigidBody,
        joint_pos: MVector3D,
        joint_qq: MQuaternion,
        limit_min_mov_xs: np.ndarray,
        limit_min_mov_ys: np.ndarray,
        limit_min_mov_zs: np.ndarray,
        limit_max_mov_xs: np.ndarray,
        limit_max_mov_ys: np.ndarray,
        limit_max_mov_zs: np.ndarray,
        limit_min_rot_xs: np.ndarray,
        limit_min_rot_ys: np.ndarray,
        limit_min_rot_zs: np.ndarray,
        limit_max_rot_xs: np.ndarray,
        limit_max_rot_ys: np.ndarray,
        limit_max_rot_zs: np.ndarray,
        spring_constant_mov_xs: np.ndarray,
        spring_constant_mov_ys: np.ndarray,
        spring_constant_mov_zs: np.ndarray,
        spring_constant_rot_xs: np.ndarray,
        spring_constant_rot_ys: np.ndarray,
        spring_constant_rot_zs: np.ndarray,
    ):
        joint_name = f"{direction_mark}|{a_rigidbody.name}|{b_rigidbody.name}"
        joint_key = f"{direction_idx}:{a_rigidbody.index:05d}:{b_rigidbody.index:05d}"

        joint_euler = joint_qq.toEulerAngles()
        joint_rotation_radians = MVector3D(
            math.radians(joint_euler.x()), math.radians(joint_euler.y()), math.radians(joint_euler.z())
        )

        joint = Joint(
            joint_name,
            joint_name,
            0,
            a_rigidbody.index,
            b_rigidbody.index,
            joint_pos,
            joint_rotation_radians,
            MVector3D(
                limit_min_mov_xs[bone_y_idx],
                limit_min_mov_ys[bone_y_idx],
                limit_min_mov_zs[bone_y_idx],
            ),
            MVector3D(
                limit_max_mov_xs[bone_y_idx],
                limit_max_mov_ys[bone_y_idx],
                limit_max_mov_zs[bone_y_idx],
            ),
            MVector3D(
                math.radians(limit_min_rot_xs[bone_y_idx]),
                math.radians(limit_min_rot_ys[bone_y_idx]),
                math.radians(limit_min_rot_zs[bone_y_idx]),
            ),
            MVector3D(
                math.radians(limit_max_rot_xs[bone_y_idx]),
                math.radians(limit_max_rot_ys[bone_y_idx]),
                math.radians(limit_max_rot_zs[bone_y_idx]),
            ),
            MVector3D(
                spring_constant_mov_xs[bone_y_idx],
                spring_constant_mov_ys[bone_y_idx],
                spring_constant_mov_zs[bone_y_idx],
            ),
            MVector3D(
                spring_constant_rot_xs[bone_y_idx],
                spring_constant_rot_ys[bone_y_idx],
                spring_constant_rot_zs[bone_y_idx],
            ),
        )
        return joint_key, joint

    def create_joint_param(self, param_joint: Joint, vv_keys: np.ndarray, coefficient: float):
        max_vy = len(vv_keys)
        middle_vy = max_vy * 0.3
        min_vy = 0
        xs = np.arange(min_vy, max_vy, step=1)

        limit_min_mov_xs = 0
        limit_min_mov_ys = 0
        limit_min_mov_zs = 0
        limit_max_mov_xs = 0
        limit_max_mov_ys = 0
        limit_max_mov_zs = 0
        limit_min_rot_xs = 0
        limit_min_rot_ys = 0
        limit_min_rot_zs = 0
        limit_max_rot_xs = 0
        limit_max_rot_ys = 0
        limit_max_rot_zs = 0
        spring_constant_mov_xs = 0
        spring_constant_mov_ys = 0
        spring_constant_mov_zs = 0
        spring_constant_rot_xs = 0
        spring_constant_rot_ys = 0
        spring_constant_rot_zs = 0

        if param_joint:
            # 縦ジョイント
            limit_min_mov_xs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, max_vy],
                            [
                                param_joint.translation_limit_min.x() / coefficient,
                                param_joint.translation_limit_min.x(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_min_mov_ys = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, max_vy],
                            [
                                param_joint.translation_limit_min.y() / coefficient,
                                param_joint.translation_limit_min.y(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_min_mov_zs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, max_vy],
                            [
                                param_joint.translation_limit_min.z() / coefficient,
                                param_joint.translation_limit_min.z(),
                            ],
                        ]
                    )
                ),
                xs,
            )

            limit_max_mov_xs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, max_vy],
                            [
                                param_joint.translation_limit_max.x() / coefficient,
                                param_joint.translation_limit_max.x(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_max_mov_ys = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, max_vy],
                            [
                                param_joint.translation_limit_max.y() / coefficient,
                                param_joint.translation_limit_max.y(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_max_mov_zs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, max_vy],
                            [
                                param_joint.translation_limit_max.z() / coefficient,
                                param_joint.translation_limit_max.z(),
                            ],
                        ]
                    )
                ),
                xs,
            )

            limit_min_rot_xs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.rotation_limit_min.x() / coefficient,
                                param_joint.rotation_limit_min.x() / coefficient,
                                param_joint.rotation_limit_min.x(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_min_rot_ys = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.rotation_limit_min.y() / coefficient,
                                param_joint.rotation_limit_min.y() / coefficient,
                                param_joint.rotation_limit_min.y(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_min_rot_zs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.rotation_limit_min.z() / coefficient,
                                param_joint.rotation_limit_min.z() / coefficient,
                                param_joint.rotation_limit_min.z(),
                            ],
                        ]
                    )
                ),
                xs,
            )

            limit_max_rot_xs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.rotation_limit_max.x() / coefficient,
                                param_joint.rotation_limit_max.x() / coefficient,
                                param_joint.rotation_limit_max.x(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_max_rot_ys = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.rotation_limit_max.y() / coefficient,
                                param_joint.rotation_limit_max.y() / coefficient,
                                param_joint.rotation_limit_max.y(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            limit_max_rot_zs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.rotation_limit_max.z() / coefficient,
                                param_joint.rotation_limit_max.z() / coefficient,
                                param_joint.rotation_limit_max.z(),
                            ],
                        ]
                    )
                ),
                xs,
            )

            spring_constant_mov_xs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.spring_constant_translation.x() / coefficient,
                                param_joint.spring_constant_translation.x() / coefficient,
                                param_joint.spring_constant_translation.x(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            spring_constant_mov_ys = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.spring_constant_translation.y() / coefficient,
                                param_joint.spring_constant_translation.y() / coefficient,
                                param_joint.spring_constant_translation.y(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            spring_constant_mov_zs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.spring_constant_translation.z() / coefficient,
                                param_joint.spring_constant_translation.z() / coefficient,
                                param_joint.spring_constant_translation.z(),
                            ],
                        ]
                    )
                ),
                xs,
            )

            spring_constant_rot_xs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.spring_constant_rotation.x() / coefficient,
                                param_joint.spring_constant_rotation.x() / coefficient,
                                param_joint.spring_constant_rotation.x(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            spring_constant_rot_ys = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.spring_constant_rotation.y() / coefficient,
                                param_joint.spring_constant_rotation.y() / coefficient,
                                param_joint.spring_constant_rotation.y(),
                            ],
                        ]
                    )
                ),
                xs,
            )
            spring_constant_rot_zs = MBezierUtils.intersect_by_x(
                bezier.Curve.from_nodes(
                    np.asfortranarray(
                        [
                            [min_vy, middle_vy, max_vy],
                            [
                                param_joint.spring_constant_rotation.z() / coefficient,
                                param_joint.spring_constant_rotation.z() / coefficient,
                                param_joint.spring_constant_rotation.z(),
                            ],
                        ]
                    )
                ),
                xs,
            )

        return (
            limit_min_mov_xs,
            limit_min_mov_ys,
            limit_min_mov_zs,
            limit_max_mov_xs,
            limit_max_mov_ys,
            limit_max_mov_zs,
            limit_min_rot_xs,
            limit_min_rot_ys,
            limit_min_rot_zs,
            limit_max_rot_xs,
            limit_max_rot_ys,
            limit_max_rot_zs,
            spring_constant_mov_xs,
            spring_constant_mov_ys,
            spring_constant_mov_zs,
            spring_constant_rot_xs,
            spring_constant_rot_ys,
            spring_constant_rot_zs,
        )

    def create_rigidbody(
        self,
        model: PmxModel,
        param_option: dict,
        material_name: str,
        virtual_vertices: dict,
        vertex_maps: dict,
        all_regist_bones: dict,
        all_bone_connected: dict,
        root_bone: Bone,
    ):
        logger.info("【%s】剛体生成", material_name, decoration=MLogger.DECORATION_LINE)

        # 剛体生成
        created_rigidbodies = {}
        # 剛体の質量
        created_rigidbody_masses = {}
        created_rigidbody_linear_dampinges = {}
        created_rigidbody_angular_dampinges = {}
        prev_rigidbody_cnt = 0

        # 剛体情報
        param_rigidbody = param_option["rigidbody"]
        # 剛体係数
        coefficient = param_option["rigidbody_coefficient"]
        # 剛体形状
        rigidbody_shape_type = param_option["rigidbody_shape_type"]

        # 親ボーンに紐付く剛体がある場合、それを利用
        parent_bone = model.bones[param_option["parent_bone_name"]]
        parent_bone_rigidbody = self.get_rigidbody(model, parent_bone.name)

        if not parent_bone_rigidbody:
            # 親ボーンに紐付く剛体がない場合、自前で作成
            parent_bone_rigidbody = RigidBody(
                parent_bone.name,
                parent_bone.english_name,
                parent_bone.index,
                param_rigidbody.collision_group,
                param_rigidbody.no_collision_group,
                0,
                MVector3D(1, 1, 1),
                parent_bone.position,
                MVector3D(),
                1,
                0.5,
                0.5,
                0,
                0,
                0,
            )
            parent_bone_rigidbody.index = len(model.rigidbodies)

            if parent_bone_rigidbody.name in model.rigidbodies:
                logger.warning("同じ剛体名が既に登録されているため、末尾に乱数を追加します。 既存剛体名: %s", parent_bone_rigidbody.name)
                parent_bone_rigidbody.name += randomname(3)

            model.rigidbodies[parent_bone.name] = parent_bone_rigidbody

        root_rigidbody = self.get_rigidbody(model, root_bone.name)
        if not root_rigidbody:
            # 中心剛体を接触なしボーン追従剛体で生成
            root_rigidbody = RigidBody(
                root_bone.name,
                root_bone.english_name,
                root_bone.index,
                param_rigidbody.collision_group,
                0,
                0,
                MVector3D(0.5, 0.5, 0.5),
                parent_bone_rigidbody.shape_position,
                MVector3D(),
                1,
                0.5,
                0.5,
                0,
                0,
                0,
            )
            root_rigidbody.index = len(model.rigidbodies)
            model.rigidbodies[root_rigidbody.name] = root_rigidbody

        for base_map_idx, regist_bones in all_regist_bones.items():
            logger.info("--【No.%s】剛体生成", base_map_idx + 1)

            vertex_map = vertex_maps[base_map_idx]
            bone_connected = all_bone_connected[base_map_idx]

            # 厚みの判定

            # 上下はY軸比較, 左右はX軸比較
            target_idx = 1 if param_option["direction"] in ["上", "下"] else 0
            target_direction = 1 if param_option["direction"] in ["上", "右"] else -1

            # キーは比較対象＋向きで昇順
            vv_keys = sorted(np.unique(vertex_map[np.where(regist_bones[:-1, :])][:, target_idx]) * target_direction)
            # 全体の比較対象の距離
            vv_distances = sorted(vertex_map[np.where(regist_bones)], key=lambda x: x[target_idx])
            distance = (
                (
                    virtual_vertices[tuple(np.nan_to_num(vv_distances[0]))].position()
                    - virtual_vertices[tuple(np.nan_to_num(vv_distances[-1]))].position()
                ).abs()
            ).data()[target_idx]
            # 厚みは比較キーの数分だけ作る
            rigidbody_limit_thicks = np.linspace(0.1, distance / 3 * 0.1, len(vv_keys))

            for v_yidx, v_xidx in zip(np.where(regist_bones[:-1, :])[0], np.where(regist_bones[:-1, :])[1]):
                rigidbody_bone_key = tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))
                vv = virtual_vertices[rigidbody_bone_key]

                prev_xidx, next_xidx, above_yidx, below_yidx = self.get_block_vidxs(
                    v_yidx, v_xidx, regist_bones, bone_connected
                )

                prev_above_bone = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, prev_xidx]))].bone
                next_above_bone = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, next_xidx]))].bone
                prev_below_bone = virtual_vertices[tuple(np.nan_to_num(vertex_map[below_yidx, prev_xidx]))].bone
                next_below_bone = virtual_vertices[tuple(np.nan_to_num(vertex_map[below_yidx, next_xidx]))].bone

                if rigidbody_shape_type == 0:
                    # 球剛体の場合
                    bone_combs = np.array(
                        list(
                            itertools.combinations(
                                [
                                    prev_above_bone.position.data(),
                                    next_above_bone.position.data(),
                                    prev_below_bone.position.data(),
                                    next_below_bone.position.data(),
                                ],
                                2,
                            )
                        )
                    )
                    x_size = np.max(np.linalg.norm(bone_combs[:, 0] - bone_combs[:, 1], ord=2, axis=1))
                    ball_size = max(0.25, x_size * 0.5)
                    shape_size = MVector3D(ball_size, ball_size, ball_size)

                elif rigidbody_shape_type == 1:
                    # 箱剛体の場合
                    rigidbody_y_idx = np.where(vv_keys == rigidbody_bone_key[target_idx] * target_direction)[0]
                    if not rigidbody_y_idx:
                        rigidbody_y_idx = 0

                    x_size = np.max(
                        np.linalg.norm(
                            np.array([prev_above_bone.position.data(), prev_below_bone.position.data()])
                            - np.array([next_above_bone.position.data(), next_below_bone.position.data()]),
                            ord=2,
                            axis=1,
                        )
                    )
                    y_size = np.max(
                        np.linalg.norm(
                            np.array([prev_above_bone.position.data(), next_above_bone.position.data()])
                            - np.array([prev_below_bone.position.data(), next_below_bone.position.data()]),
                            ord=2,
                            axis=1,
                        )
                    )
                    shape_size = MVector3D(
                        x_size * 0.3, max(0.25, y_size * 0.5), rigidbody_limit_thicks[rigidbody_y_idx]
                    )
                else:
                    # TODO カプセル剛体の場合
                    pass

                tail_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[(below_yidx, v_xidx)]))]

                shape_position = MVector3D(
                    np.mean(
                        [
                            vv.bone.position.data(),
                            tail_vv.bone.position.data(),
                        ],
                        axis=0,
                    )
                )

                # ボーン進行方向(x)
                x_direction_pos = (vv.bone.position - tail_vv.bone.position).normalized()
                # ボーン進行方向に対しての横軸(y)
                y_direction_pos = (next_below_bone.position - prev_below_bone.position).normalized()
                # ボーン進行方向に対しての縦軸(z)
                z_direction_pos = MVector3D.crossProduct(y_direction_pos, x_direction_pos).normalized()
                # y_direction_pos = ((vv.normal().normalized() + tail_vv.normal().normalized()) / 2).normalized() * -1
                shape_qq = MQuaternion.fromDirection(z_direction_pos, x_direction_pos)
                shape_euler = shape_qq.toEulerAngles()
                shape_rotation_radians = MVector3D(
                    math.radians(shape_euler.x()), math.radians(shape_euler.y()), math.radians(shape_euler.z())
                )

                # 根元は物理演算 + Bone位置合わせ、それ以降は物理剛体
                mode = 2 if 0 == v_yidx else 1
                mass = param_rigidbody.param.mass * shape_size.x() * shape_size.y() * shape_size.z()
                linear_damping = (
                    param_rigidbody.param.linear_damping * shape_size.x() * shape_size.y() * shape_size.z()
                )
                angular_damping = (
                    param_rigidbody.param.angular_damping * shape_size.x() * shape_size.y() * shape_size.z()
                )

                vv.rigidbody = RigidBody(
                    vv.bone.name,
                    vv.bone.name,
                    vv.bone.index,
                    param_rigidbody.collision_group,
                    param_rigidbody.no_collision_group,
                    rigidbody_shape_type,
                    shape_size,
                    shape_position,
                    shape_rotation_radians,
                    mass,
                    linear_damping,
                    angular_damping,
                    param_rigidbody.param.restitution,
                    param_rigidbody.param.friction,
                    mode,
                )
                vv.rigidbody_qq = shape_qq

                # 別途保持しておく
                created_rigidbodies[vv.rigidbody.name] = vv.rigidbody
                created_rigidbody_masses[vv.rigidbody.name] = mass
                created_rigidbody_linear_dampinges[vv.rigidbody.name] = linear_damping
                created_rigidbody_angular_dampinges[vv.rigidbody.name] = angular_damping

                if len(created_rigidbodies) > 0 and len(created_rigidbodies) // 50 > prev_rigidbody_cnt:
                    logger.info("-- -- 【No.%s】剛体: %s個目:終了", base_map_idx + 1, len(created_rigidbodies))
                    prev_rigidbody_cnt = len(created_rigidbodies) // 50

        min_mass = 0
        min_linear_damping = 0
        min_angular_damping = 0

        max_mass = 0
        max_linear_damping = 0
        max_angular_damping = 0

        if len(created_rigidbody_masses.values()) > 0:
            min_mass = np.min(list(created_rigidbody_masses.values()))
            min_linear_damping = np.min(list(created_rigidbody_linear_dampinges.values()))
            min_angular_damping = np.min(list(created_rigidbody_angular_dampinges.values()))

            max_mass = np.max(list(created_rigidbody_masses.values()))
            max_linear_damping = np.max(list(created_rigidbody_linear_dampinges.values()))
            max_angular_damping = np.max(list(created_rigidbody_angular_dampinges.values()))

        for rigidbody_name in sorted(created_rigidbodies.keys()):
            # 剛体を登録
            rigidbody = created_rigidbodies[rigidbody_name]
            rigidbody.index = len(model.rigidbodies)

            # 質量と減衰は面積に応じた値に変換
            if min_mass != max_mass:
                rigidbody.param.mass = calc_ratio(
                    rigidbody.param.mass,
                    max_mass,
                    min_mass,
                    param_rigidbody.param.mass,
                    param_rigidbody.param.mass * coefficient,
                )
            if min_linear_damping != max_linear_damping:
                rigidbody.param.linear_damping = calc_ratio(
                    rigidbody.param.linear_damping,
                    max_linear_damping,
                    min_linear_damping,
                    param_rigidbody.param.linear_damping,
                    min(0.9999999, param_rigidbody.param.linear_damping * coefficient),
                )
            if min_angular_damping != max_angular_damping:
                rigidbody.param.angular_damping = calc_ratio(
                    rigidbody.param.angular_damping,
                    max_angular_damping,
                    min_angular_damping,
                    param_rigidbody.param.angular_damping,
                    min(0.9999999, param_rigidbody.param.angular_damping * coefficient),
                )

            if rigidbody.name in model.rigidbodies:
                logger.warning("同じ剛体名が既に登録されているため、末尾に乱数を追加します。 既存剛体名: %s", rigidbody.name)
                rigidbody.name += randomname(3)

            model.rigidbodies[rigidbody.name] = rigidbody
            logger.debug(f"rigidbody: {rigidbody}")

        logger.info("-- 剛体: %s個目:終了", len(created_rigidbodies))

        # TODO バランサー剛体

        return root_rigidbody

    def create_back_weight(
        self, model: PmxModel, param_option: dict, material_name: str, virtual_vertices: dict, back_vertices: list
    ):
        if param_option["back_material_name"]:
            # 表面で残った裏頂点と裏材質で指定されている頂点を全部対象とする
            back_vertices += list(model.material_vertices[param_option["back_material_name"]])

        if not back_vertices:
            return

        logger.info("【%s】裏ウェイト生成", material_name, decoration=MLogger.DECORATION_LINE)

        weight_cnt = 0
        prev_weight_cnt = 0

        front_vertices = {}
        for vv in virtual_vertices.values():
            for v in vv.real_vertices:
                front_vertices[v.index] = v.position.data()

        for vertex_idx in back_vertices:
            bv = model.vertex_dict[vertex_idx]

            # 各頂点の位置との差分から距離を測る
            bv_distances = np.linalg.norm(
                (np.array(list(front_vertices.values())) - bv.position.data()), ord=2, axis=1
            )

            # 直近頂点INDEXのウェイトを転写
            copy_front_vertex_idx = list(front_vertices.keys())[np.argmin(bv_distances)]
            bv.deform = copy.deepcopy(model.vertex_dict[copy_front_vertex_idx].deform)

            weight_cnt += 1
            if weight_cnt > 0 and weight_cnt // 200 > prev_weight_cnt:
                logger.info("-- 裏頂点ウェイト: %s個目:終了", weight_cnt)
                prev_weight_cnt = weight_cnt // 200

        logger.info("-- 裏頂点ウェイト: %s個目:終了", weight_cnt)

    def create_remaining_weight(
        self, model: PmxModel, param_option: dict, material_name: str, virtual_vertices: dict, remaining_vertices: dict
    ):
        logger.info("【%s】残ウェイト生成", material_name, decoration=MLogger.DECORATION_LINE)

        vertex_cnt = 0
        prev_vertex_cnt = 0

        for vkey, vv in remaining_vertices.items():
            surround_bone_vvs = self.get_surround_vvs(vv, virtual_vertices)

            if len(surround_bone_vvs) < 1:
                logger.warning("残ウェイト計算で関連ボーンが見つからなかった為、親ボーンウェイトのままスキップします。: 対象頂点[%s]", vv.vidxs())
                continue

            weigth_cnt = 1 if len(surround_bone_vvs) < 2 else 2 if len(surround_bone_vvs) < 4 else 4

            # 位置をリストアップ
            surround_bone_positions = []
            for bone_vv in surround_bone_vvs:
                surround_bone_positions.append(virtual_vertices[bone_vv].position().data())

            # 近いのからウェイト対象分選ぶ
            surround_distances = np.linalg.norm(
                np.array(surround_bone_positions) - vv.position().data(), ord=2, axis=1
            )
            weight_bone_vvs = np.array(surround_bone_vvs)[np.argsort(surround_distances)[:weigth_cnt]]
            weight_bone_distances = np.sort(surround_distances)[:weigth_cnt]
            reverse_weights = weight_bone_distances / weight_bone_distances.sum(axis=0, keepdims=1)
            target_weights = 1 - reverse_weights
            deform_weights = target_weights / target_weights.sum(axis=0, keepdims=1)

            for rv in vv.real_vertices:
                if weigth_cnt == 1:
                    rv.deform = Bdef1(virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[0]))].bone.index)
                elif weigth_cnt == 2:
                    rv.deform = Bdef2(
                        virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[0]))].bone.index,
                        virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[1]))].bone.index,
                        deform_weights[0],
                    )
                else:
                    rv.deform = Bdef4(
                        virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[0]))].bone.index,
                        virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[1]))].bone.index,
                        virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[2]))].bone.index,
                        virtual_vertices[tuple(np.nan_to_num(weight_bone_vvs[3]))].bone.index,
                        deform_weights[0],
                        deform_weights[1],
                        deform_weights[2],
                        deform_weights[3],
                    )

            vertex_cnt += 1

            if vertex_cnt > 0 and vertex_cnt // 10 > prev_vertex_cnt:
                logger.info("-- 残ウェイト: %s個目:終了", vertex_cnt)
                prev_vertex_cnt = vertex_cnt // 10

    def get_surround_vvs(self, vv: VirtualVertex, virtual_vertices: dict, surround_bone_vvs=[], loop=0):
        surround_bone_vvs = list(set(surround_bone_vvs))

        if len(surround_bone_vvs) > 6 or loop > 5:
            return surround_bone_vvs

        for cvv in vv.connected_vvs:
            if (
                virtual_vertices[cvv].bone
                and virtual_vertices[cvv].bone.getVisibleFlag()
                and cvv not in surround_bone_vvs
            ):
                surround_bone_vvs.append(cvv)

            surround_bone_vvs.extend(
                self.get_surround_vvs(virtual_vertices[cvv], virtual_vertices, surround_bone_vvs, loop + 1)
            )

        surround_bone_vvs = list(set(surround_bone_vvs))

        if len(surround_bone_vvs) > 6 or loop > 5:
            return surround_bone_vvs

        return surround_bone_vvs

    def create_weight(
        self,
        model: PmxModel,
        param_option: dict,
        material_name: str,
        virtual_vertices: dict,
        vertex_maps: dict,
        all_regist_bones: dict,
        all_bone_vertical_distances: dict,
        all_bone_horizonal_distances: dict,
        all_bone_connected: dict,
    ):
        logger.info("【%s】ウェイト生成", material_name, decoration=MLogger.DECORATION_LINE)

        for base_map_idx, regist_bones in all_regist_bones.items():
            logger.info("--【No.%s】ウェイト分布判定", base_map_idx + 1)

            bone_connected = all_bone_connected[base_map_idx]
            vertex_map = vertex_maps[base_map_idx]

            # ウェイト分布
            prev_weight_cnt = 0
            weight_cnt = 0

            for v_yidx in range(regist_bones.shape[0]):
                for v_xidx in range(regist_bones.shape[1]):
                    if np.isnan(vertex_map[v_yidx, v_xidx]).any():
                        continue

                    vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))]

                    if regist_bones[v_yidx, v_xidx]:
                        if v_yidx < regist_bones.shape[0] - 1:
                            target_v_yidx = v_yidx
                        else:
                            # Y末端は登録対象外なので、ひとつ上のをそのまま割り当てる
                            target_v_yidx = (
                                v_yidx
                                if v_yidx < regist_bones.shape[0] - 1
                                else np.max(np.where(regist_bones[:v_yidx, :]), axis=1)[0]
                            )

                        vv.deform = Bdef1(
                            virtual_vertices[tuple(np.nan_to_num(vertex_map[target_v_yidx, v_xidx]))].bone.index
                        )

                        # 頂点位置にボーンが登録されている場合、BDEF1登録対象
                        for tv in vv.real_vertices:
                            tv.deform = vv.deform
                    elif regist_bones[v_yidx, :].any():
                        # 同じY位置にボーンがある場合、横のBDEF2登録対象
                        # 末端ボーンにはウェイトを割り当てない
                        target_v_yidx = (
                            v_yidx
                            if v_yidx < regist_bones.shape[0] - 1
                            else np.max(np.where(regist_bones[:v_yidx, :]), axis=1)[0]
                        )
                        prev_xidx = np.max(np.where(regist_bones[target_v_yidx, : (v_xidx + 1)]))
                        if v_xidx < regist_bones.shape[1] - 1 and regist_bones[target_v_yidx, (v_xidx + 1) :].any():
                            next_xidx = v_xidx + 1 + np.min(np.where(regist_bones[target_v_yidx, (v_xidx + 1) :]))
                            regist_next_xidx = next_xidx
                        else:
                            next_xidx = v_xidx + 1
                            regist_next_xidx = 0

                        prev_weight = np.sum(
                            all_bone_horizonal_distances[base_map_idx][target_v_yidx, prev_xidx:v_xidx]
                        ) / np.sum(all_bone_horizonal_distances[base_map_idx][target_v_yidx, prev_xidx:next_xidx])

                        vv.deform = Bdef2(
                            virtual_vertices[tuple(np.nan_to_num(vertex_map[target_v_yidx, prev_xidx]))].bone.index,
                            virtual_vertices[
                                tuple(np.nan_to_num(vertex_map[target_v_yidx, regist_next_xidx]))
                            ].bone.index,
                            1 - prev_weight,
                        )

                        for tv in vv.real_vertices:
                            tv.deform = vv.deform

                    elif regist_bones[:, v_xidx].any():
                        # 同じX位置にボーンがある場合、縦のBDEF2登録対象
                        prev_xidx, next_xidx, above_yidx, below_yidx = self.get_block_vidxs(
                            v_yidx, v_xidx, regist_bones, bone_connected
                        )

                        if below_yidx == regist_bones.shape[0] - 1:
                            # 末端がある場合、上のボーンでBDEF1
                            for rv in vv.real_vertices:
                                rv.deform = Bdef1(
                                    virtual_vertices[tuple(np.nan_to_num(vertex_map[above_yidx, v_xidx]))].bone.index
                                )
                        else:
                            above_weight = np.sum(
                                all_bone_vertical_distances[base_map_idx][
                                    (above_yidx + 1) : (v_yidx + 1), (v_xidx - 1)
                                ]
                            ) / np.sum(
                                all_bone_vertical_distances[base_map_idx][
                                    (above_yidx + 1) : (below_yidx + 1), (v_xidx - 1)
                                ]
                            )

                            vv.deform = Bdef2(
                                virtual_vertices[tuple(np.nan_to_num(vertex_map[above_yidx, v_xidx]))].bone.index,
                                virtual_vertices[tuple(np.nan_to_num(vertex_map[below_yidx, v_xidx]))].bone.index,
                                1 - above_weight,
                            )

                            for tv in vv.real_vertices:
                                tv.deform = vv.deform
                    else:
                        prev_xidx, next_xidx, above_yidx, below_yidx = self.get_block_vidxs(
                            v_yidx, v_xidx, regist_bones, bone_connected
                        )

                        if regist_bones[:v_yidx, v_xidx:].any():
                            target_next_xidx = next_xidx
                        else:
                            # 最後の頂点の場合、とりあえず次の距離を対象とする
                            next_xidx = v_xidx + 1
                            target_next_xidx = 0

                        prev_above_weight = (
                            np.sum(all_bone_vertical_distances[base_map_idx][v_yidx:below_yidx, (v_xidx - 1)])
                            / np.sum(all_bone_vertical_distances[base_map_idx][above_yidx:below_yidx, (v_xidx - 1)])
                        ) * (
                            np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, v_xidx:next_xidx])
                            / np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, prev_xidx:next_xidx])
                        )

                        next_above_weight = (
                            np.sum(all_bone_vertical_distances[base_map_idx][v_yidx:below_yidx, (v_xidx - 1)])
                            / np.sum(all_bone_vertical_distances[base_map_idx][above_yidx:below_yidx, (v_xidx - 1)])
                        ) * (
                            np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, prev_xidx:v_xidx])
                            / np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, prev_xidx:next_xidx])
                        )

                        prev_below_weight = (
                            np.sum(all_bone_vertical_distances[base_map_idx][above_yidx:v_yidx, (v_xidx - 1)])
                            / np.sum(all_bone_vertical_distances[base_map_idx][above_yidx:below_yidx, (v_xidx - 1)])
                        ) * (
                            np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, v_xidx:next_xidx])
                            / np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, prev_xidx:next_xidx])
                        )

                        next_below_weight = (
                            np.sum(all_bone_vertical_distances[base_map_idx][above_yidx:v_yidx, (v_xidx - 1)])
                            / np.sum(all_bone_vertical_distances[base_map_idx][above_yidx:below_yidx, (v_xidx - 1)])
                        ) * (
                            np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, prev_xidx:v_xidx])
                            / np.sum(all_bone_horizonal_distances[base_map_idx][v_yidx, prev_xidx:next_xidx])
                        )

                        if below_yidx == regist_bones.shape[0] - 1:
                            prev_above_weight += prev_below_weight
                            next_above_weight += next_below_weight

                            # ほぼ0のものは0に置換（円周用）
                            total_weights = np.array([prev_above_weight, next_above_weight])
                            total_weights[np.isclose(total_weights, 0, equal_nan=True)] = 0

                            if np.count_nonzero(total_weights):
                                deform_weights = total_weights / total_weights.sum(axis=0, keepdims=1)

                                vv.deform = Bdef4(
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[above_yidx, prev_xidx]))
                                    ].bone.index,
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[above_yidx, target_next_xidx]))
                                    ].bone.index,
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[below_yidx, prev_xidx]))
                                    ].bone.index,
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[below_yidx, target_next_xidx]))
                                    ].bone.index,
                                    deform_weights[0],
                                    deform_weights[1],
                                    0,
                                    0,
                                )

                                for tv in vv.real_vertices:
                                    tv.deform = vv.deform
                        else:
                            # ほぼ0のものは0に置換（円周用）
                            total_weights = np.array(
                                [prev_above_weight, next_above_weight, prev_below_weight, next_below_weight]
                            )
                            total_weights[np.isclose(total_weights, 0, equal_nan=True)] = 0

                            if np.count_nonzero(total_weights):
                                deform_weights = total_weights / total_weights.sum(axis=0, keepdims=1)

                                vv.deform = Bdef4(
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[above_yidx, prev_xidx]))
                                    ].bone.index,
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[above_yidx, target_next_xidx]))
                                    ].bone.index,
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[below_yidx, prev_xidx]))
                                    ].bone.index,
                                    virtual_vertices[
                                        tuple(np.nan_to_num(vertex_map[below_yidx, target_next_xidx]))
                                    ].bone.index,
                                    deform_weights[0],
                                    deform_weights[1],
                                    deform_weights[2],
                                    deform_weights[3],
                                )

                                for tv in vv.real_vertices:
                                    tv.deform = vv.deform

                    weight_cnt += len(vv.real_vertices)
                    if weight_cnt > 0 and weight_cnt // 1000 > prev_weight_cnt:
                        logger.info("-- --【No.%s】頂点ウェイト: %s個目:終了", base_map_idx + 1, weight_cnt)
                        prev_weight_cnt = weight_cnt // 1000

    def create_bone(
        self,
        model: PmxModel,
        param_option: dict,
        material_name: str,
        virtual_vertices: dict,
        vertex_maps: dict,
        vertex_map_orders: dict,
    ):
        logger.info("【%s】ボーン生成", material_name, decoration=MLogger.DECORATION_LINE)

        # 中心ボーン生成

        # 略称
        abb_name = param_option["abb_name"]
        # 表示枠名
        display_name = f"{abb_name}:{material_name}"
        # 親ボーン
        parent_bone = model.bones[param_option["parent_bone_name"]]

        # 表示枠定義
        model.display_slots[display_name] = DisplaySlot(display_name, display_name, 0, 0)

        # 中心ボーン
        root_bone = Bone(
            f"{abb_name}中心",
            f"{abb_name}Root",
            parent_bone.position,
            parent_bone.index,
            0,
            0x0000 | 0x0002 | 0x0004 | 0x0008 | 0x0010,
        )
        if root_bone.name in model.bones:
            logger.warning("同じボーン名が既に登録されているため、末尾に乱数を追加します。 既存ボーン名: %s", root_bone.name)
            root_bone.name += randomname(3)

        root_bone.index = len(model.bones)
        model.bones[root_bone.name] = root_bone
        model.bone_indexes[root_bone.index] = root_bone.name

        logger.info("【%s】頂点距離の算出", material_name)

        all_bone_horizonal_distances = {}
        all_bone_vertical_distances = {}
        all_bone_connected = {}

        for base_map_idx, vertex_map in enumerate(vertex_maps):
            logger.info("--【No.%s】頂点距離算出", base_map_idx + 1)

            prev_vertex_cnt = 0
            vertex_cnt = 0

            bone_horizonal_distances = np.zeros((vertex_map.shape[0], vertex_map.shape[1]))
            bone_vertical_distances = np.zeros((vertex_map.shape[0] - 1, vertex_map.shape[1] - 1))
            bone_connected = np.zeros((vertex_map.shape[0], vertex_map.shape[1]), dtype=np.int)

            # 各頂点の距離（円周っぽい可能性があるため、頂点一個ずつで測る）
            for v_yidx in range(vertex_map.shape[0]):
                v_xidx = -1
                for v_xidx in range(0, vertex_map.shape[1] - 1):
                    if (
                        not np.isnan(vertex_map[v_yidx, v_xidx]).any()
                        and not np.isnan(vertex_map[v_yidx, v_xidx + 1]).any()
                    ):
                        now_v_vec = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))].position()
                        next_v_vec = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx + 1]))].position()
                        bone_horizonal_distances[v_yidx, v_xidx] = now_v_vec.distanceToPoint(next_v_vec)

                        if (
                            tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))
                            in virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx + 1]))].connected_vvs
                        ):
                            # 前の仮想頂点と繋がっている場合、True
                            bone_connected[v_yidx, v_xidx] = True

                    if (
                        v_yidx < vertex_map.shape[0] - 1
                        and not np.isnan(vertex_map[v_yidx, v_xidx]).any()
                        and not np.isnan(vertex_map[v_yidx + 1, v_xidx]).any()
                    ):
                        now_v_vec = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))].position()
                        next_v_vec = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx + 1, v_xidx]))].position()
                        bone_vertical_distances[v_yidx, v_xidx] = now_v_vec.distanceToPoint(next_v_vec)

                    vertex_cnt += 1
                    if vertex_cnt > 0 and vertex_cnt // 1000 > prev_vertex_cnt:
                        logger.info("-- --【No.%s】頂点距離算出: %s個目:終了", base_map_idx + 1, vertex_cnt)
                        prev_vertex_cnt = vertex_cnt // 1000

                v_xidx += 1
                if not np.isnan(vertex_map[v_yidx, v_xidx]).any() and not np.isnan(vertex_map[v_yidx, 0]).any():
                    # 輪を描いたのも入れとく(ウェイト対象取得の時に範囲指定入るからここでは強制)
                    if (
                        tuple(np.nan_to_num(vertex_map[v_yidx, 0]))
                        in virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))].connected_vvs
                    ):
                        # 横の仮想頂点と繋がっている場合、Trueで有効な距離を入れておく
                        bone_connected[v_yidx, v_xidx] = True

                        now_v_vec = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))].position()
                        next_v_vec = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, 0]))].position()
                        bone_horizonal_distances[v_yidx, v_xidx] = now_v_vec.distanceToPoint(next_v_vec)
                    else:
                        # とりあえずINT最大値を入れておく
                        bone_horizonal_distances[v_yidx, v_xidx] = np.iinfo(np.int).max

            all_bone_horizonal_distances[base_map_idx] = bone_horizonal_distances
            all_bone_vertical_distances[base_map_idx] = bone_vertical_distances
            all_bone_connected[base_map_idx] = bone_connected

        # 全体通してのX番号
        prev_xs = [0]
        all_regist_bones = {}
        for base_map_idx in vertex_map_orders:
            logger.info("--【No.%s】ボーン生成", base_map_idx + 1)

            prev_bone_cnt = 0
            bone_cnt = 0

            vertex_map = vertex_maps[base_map_idx]

            # ボーン登録有無
            regist_bones = np.zeros((vertex_map.shape[0], vertex_map.shape[1]), dtype=np.int)
            all_regist_bones[base_map_idx] = regist_bones

            if param_option["density_type"] == logger.transtext("距離"):
                median_vertical_distance = np.median(
                    all_bone_vertical_distances[base_map_idx][:, int(vertex_map.shape[1] / 2)]
                )
                median_horizonal_distance = np.median(
                    all_bone_horizonal_distances[base_map_idx][int(vertex_map.shape[0] / 2), :]
                )

                logger.debug(
                    f"median_horizonal_distance: {round(median_horizonal_distance, 4)}, median_vertical_distance: {round(median_vertical_distance, 4)}"
                )

                # 間隔が距離タイプの場合、均等になるように間を空ける
                y_regists = np.zeros(vertex_map.shape[0], dtype=np.int)
                prev_y_regist = 0
                for v_yidx in range(vertex_map.shape[0]):
                    if v_yidx in [0, vertex_map.shape[0] - 1]:
                        # 最初は必ず登録
                        y_regists[v_yidx] = True
                        continue

                    if (
                        np.sum(
                            all_bone_vertical_distances[base_map_idx][
                                (prev_y_regist + 1) : (v_yidx + 1), int(vertex_map.shape[1] / 2)
                            ]
                        )
                        > median_vertical_distance * param_option["vertical_bone_density"]
                    ):
                        # 前の登録ボーンから一定距離離れたら登録対象
                        y_regists[v_yidx] = True
                        prev_y_regist = v_yidx

                x_regists = np.zeros(vertex_map.shape[1], dtype=np.int)
                prev_x_regist = 0
                for v_xidx in range(vertex_map.shape[1]):
                    if v_xidx in [0, vertex_map.shape[1] - 1]:
                        # 最初は必ず登録
                        x_regists[v_xidx] = True
                        continue

                    if (
                        np.sum(
                            all_bone_horizonal_distances[base_map_idx][
                                int(vertex_map.shape[0] / 2), (prev_x_regist + 1) : (v_xidx + 1)
                            ]
                        )
                        > median_horizonal_distance * param_option["horizonal_bone_density"]
                    ):
                        # 前の登録ボーンから一定距離離れたら登録対象
                        x_regists[v_xidx] = True
                        prev_x_regist = v_xidx

                for v_yidx, y_regist in enumerate(y_regists):
                    for v_xidx, x_regist in enumerate(x_regists):
                        regist_bones[v_yidx, v_xidx] = y_regist and x_regist

            else:
                # 間隔が頂点タイプの場合、規則的に間を空ける
                for v_yidx in list(range(0, vertex_map.shape[0], param_option["vertical_bone_density"])) + [
                    vertex_map.shape[0] - 1
                ]:
                    for v_xidx in range(0, vertex_map.shape[1], param_option["horizonal_bone_density"]):
                        if not np.isnan(vertex_map[v_yidx, v_xidx]).any():
                            regist_bones[v_yidx, v_xidx] = True
                    if not all_bone_connected[base_map_idx][v_yidx, vertex_map.shape[1] - 1]:
                        # 繋がってない場合、最後に追加する
                        if not np.isnan(vertex_map[v_yidx, v_xidx]).any():
                            regist_bones[v_yidx, vertex_map.shape[1] - 1] = True

            for v_yidx in range(vertex_map.shape[0]):
                for v_xidx in range(vertex_map.shape[1]):
                    if np.isnan(vertex_map[v_yidx, v_xidx]).any():
                        continue

                    v_yno = v_yidx + 1
                    v_xno = v_xidx + max(prev_xs) + 1

                    vkey = tuple(np.nan_to_num(vertex_map[v_yidx, v_xidx]))
                    vv = virtual_vertices[vkey]

                    # 親は既にモデルに登録済みのものを選ぶ
                    parent_bone = None
                    for parent_v_yidx in range(v_yidx - 1, -1, -1):
                        parent_bone = virtual_vertices[tuple(np.nan_to_num(vertex_map[parent_v_yidx, v_xidx]))].bone
                        if parent_bone and parent_bone.name in model.bones:
                            # 登録されていたら終了
                            break
                        else:
                            parent_bone = None
                    if not parent_bone:
                        # 最後まで登録されている親ボーンが見つからなければ、ルート
                        parent_bone = root_bone

                    # ひとつ前も既にモデルに登録済みのものを選ぶ
                    prev_bone = None
                    for prev_v_xidx in range(v_xidx - 1, -1, -1):
                        if np.isnan(vertex_map[v_yidx, prev_v_xidx]).any():
                            continue

                        prev_bone = virtual_vertices[tuple(np.nan_to_num(vertex_map[v_yidx, prev_v_xidx]))].bone
                        if prev_bone and prev_bone.name in model.bones:
                            # 登録されていたら終了
                            break

                    if not vv.bone:
                        # ボーン仮登録
                        is_add_random = False
                        bone_name = self.get_bone_name(abb_name, v_yno, v_xno)
                        if bone_name in model.bones:
                            # 仮登録の時点で乱数は後ろに付けておくが、メッセージは必要なのだけ出す
                            is_add_random = True
                            bone_name += randomname(3)

                        bone = Bone(bone_name, bone_name, vv.position().copy(), parent_bone.index, 0, 0x0000 | 0x0002)
                        bone.index = len(model.bones)
                        bone.local_z_vector = vv.normal().copy()

                        bone.parent_index = parent_bone.index
                        bone.local_x_vector = (bone.position - parent_bone.position).normalized()
                        bone.local_z_vector *= MVector3D(-1, 1, -1)
                        bone.flag |= 0x0800

                        if v_yidx > 0:
                            # 親ボーンを表示対象にする
                            parent_bone.flag |= 0x0008 | 0x0010
                            model.display_slots[display_name].references.append((0, parent_bone.index))

                        vv.bone = bone

                        if regist_bones[v_yidx, v_xidx]:
                            bone.index = len(model.bones)

                            # 親ボーンの表示先も同時設定
                            if parent_bone != root_bone:
                                parent_bone.tail_index = bone.index
                                parent_bone.local_x_vector = (bone.position - parent_bone.position).normalized()
                                parent_bone.flag |= 0x0001

                            if is_add_random:
                                logger.warning("同じボーン名が既に登録されているため、末尾に乱数を追加します。 既存ボーン名: %s", bone.name)

                            # 登録対象である場合
                            model.bones[bone.name] = bone
                            model.bone_indexes[bone.index] = bone.name

                        logger.debug(f"tmp_all_bones: {bone.name}, pos: {bone.position.to_log()}")

                        bone_cnt += 1
                        if bone_cnt > 0 and bone_cnt // 1000 > prev_bone_cnt:
                            logger.info("-- --【No.%s】ボーン生成: %s個目:終了", base_map_idx + 1, bone_cnt)
                            prev_bone_cnt = bone_cnt // 1000

            prev_xs.extend(max(prev_xs) + np.array(list(range(vertex_map.shape[1]))) + 1)

        return (
            root_bone,
            virtual_vertices,
            all_regist_bones,
            all_bone_vertical_distances,
            all_bone_horizonal_distances,
            all_bone_connected,
        )

    def get_bone_name(self, abb_name: str, v_yno: int, v_xno: int):
        return f"{abb_name}-{(v_yno):03d}-{(v_xno):03d}"

    def create_vertex_map(self, model: PmxModel, param_option: dict, material_name: str, target_vertices: list):
        # 閾値
        threshold = param_option["threshold"]

        # 裏面頂点リスト
        back_vertices = []
        # 残頂点リスト
        remaining_vertices = {}

        # 方向に応じて判定値を変える
        # デフォルトは下
        base_vertical_axis = MVector3D(0, -1, 0)
        if param_option["direction"] == logger.transtext("上"):
            base_vertical_axis = MVector3D(0, 1, 0)
        elif param_option["direction"] == logger.transtext("右"):
            base_vertical_axis = MVector3D(-1, 0, 0)
        elif param_option["direction"] == logger.transtext("左"):
            base_vertical_axis = MVector3D(1, 0, 0)
        base_reverse_axis = MVector3D(np.logical_not(np.abs(base_vertical_axis.data())))

        logger.info("%s: 材質頂点の傾き算出", material_name)

        parent_bone = model.bones[param_option["parent_bone_name"]]

        # 一旦全体の位置を把握
        vertex_positions = {}
        for vertex_idx in model.material_vertices[material_name]:
            if vertex_idx not in target_vertices:
                continue
            vertex_positions[vertex_idx] = model.vertex_dict[vertex_idx].position.data()

        # 各頂点の位置との差分から距離を測る
        v_distances = np.linalg.norm(
            (np.array(list(vertex_positions.values())) - parent_bone.position.data()), ord=2, axis=1
        )
        # 親ボーンに最も近い頂点
        nearest_vertex_idx = list(vertex_positions.keys())[np.argmin(v_distances)]
        # 親ボーンに最も遠い頂点
        farest_vertex_idx = list(vertex_positions.keys())[np.argmax(v_distances)]
        # 材質全体の傾き
        material_direction = (
            (model.vertex_dict[farest_vertex_idx].position - model.vertex_dict[nearest_vertex_idx].position)
            .abs()
            .normalized()
            .data()[np.where(np.abs(base_vertical_axis.data()))]
        )[0]
        material_direction = 0 if material_direction < 0.1 else 0.1

        logger.info("%s: 仮想頂点リストの生成", material_name)

        virtual_vertices = {}

        # nan_to_num対策(落とさない為)
        nan_key = (0, 0, 0)
        virtual_vertices[nan_key] = VirtualVertex(nan_key)
        virtual_vertices[nan_key].append([], [], [])
        virtual_vertices[nan_key].bone = parent_bone

        edge_pair_lkeys = {}
        for index_idx in model.material_indices[material_name]:
            # 頂点の組み合わせから面INDEXを引く
            if (
                model.indices[index_idx][0] not in target_vertices
                or model.indices[index_idx][1] not in target_vertices
                or model.indices[index_idx][2] not in target_vertices
            ):
                # 3つ揃ってない場合、スルー
                continue

            for v0_idx, v1_idx, v2_idx in zip(
                model.indices[index_idx],
                model.indices[index_idx][1:] + [model.indices[index_idx][0]],
                [model.indices[index_idx][2]] + model.indices[index_idx][:-1],
            ):
                v0 = model.vertex_dict[v0_idx]
                v1 = model.vertex_dict[v1_idx]
                v2 = model.vertex_dict[v2_idx]

                v0_key = v0.position.to_key(threshold)
                v1_key = v1.position.to_key(threshold)
                v2_key = v2.position.to_key(threshold)

                # 一旦ルートボーンにウェイトを一括置換
                v0.deform = Bdef1(parent_bone.index)

                # 面垂線
                vv1 = v1.position - v0.position
                vv2 = v2.position - v1.position
                surface_normal = MVector3D.crossProduct(vv1, vv2).normalized() * base_reverse_axis

                # 親ボーンに対する向き
                parent_direction = (v0.position - parent_bone.position).normalized() * base_reverse_axis

                # 親ボーンの向きとの内積
                normal_dot = MVector3D.dotProduct(surface_normal, parent_direction)
                logger.debug(
                    f"index[{index_idx}], v0[{v0.index}:{v0_key}], sn[{surface_normal.to_log()}], pd[{parent_direction.to_log()}], dot[{round(normal_dot, 5)}]"
                )

                # 面法線と同じ向き場合、辺キー生成（表面のみを対象とする）
                if normal_dot >= material_direction:
                    lkey = (min(v0_key, v1_key), max(v0_key, v1_key))
                    if lkey not in edge_pair_lkeys:
                        edge_pair_lkeys[lkey] = []
                    if index_idx not in edge_pair_lkeys[lkey]:
                        edge_pair_lkeys[lkey].append(index_idx)

                    if v0_key not in virtual_vertices:
                        virtual_vertices[v0_key] = VirtualVertex(v0_key)

                    # 仮想頂点登録（該当頂点対象）
                    virtual_vertices[v0_key].append([v0], [v1_key, v2_key], [index_idx])

                    # 残頂点リストにまずは登録
                    if v0_key not in remaining_vertices:
                        remaining_vertices[v0_key] = virtual_vertices[v0_key]
                else:
                    # 裏面に登録
                    if v0.index not in back_vertices:
                        back_vertices.append(v0.index)

        if not virtual_vertices:
            logger.warning("対象範囲となる頂点が取得できなかった為、処理を終了します", decoration=MLogger.DECORATION_BOX)
            return None, None, None, None

        if not edge_pair_lkeys:
            logger.warning("対象範囲にエッジが見つけられなかった為、処理を終了します。\n面が表裏反転してないかご確認ください。", decoration=MLogger.DECORATION_BOX)
            return None, None, None, None

        if logger.is_debug_level():
            logger.debug("--------------------------")
            for key, virtual_vertex in virtual_vertices.items():
                logger.debug(f"[{key}] {virtual_vertex}")

            logger.debug("--------------------------")
            for (min_key, max_key), indexes in edge_pair_lkeys.items():
                logger.debug(
                    f"[{min_key}:{virtual_vertices[min_key].vidxs()}, {max_key}:{virtual_vertices[max_key].vidxs()}] {indexes}"
                )

        edge_line_pairs = {}
        for (min_vkey, max_vkey), line_iidxs in edge_pair_lkeys.items():
            if len(line_iidxs) == 1:
                if min_vkey not in edge_line_pairs:
                    edge_line_pairs[min_vkey] = []
                if max_vkey not in edge_line_pairs:
                    edge_line_pairs[max_vkey] = []

                edge_line_pairs[min_vkey].append(max_vkey)
                edge_line_pairs[max_vkey].append(min_vkey)

        logger.info("%s: エッジの抽出準備", material_name)

        # エッジを繋いでいく
        all_edge_lines = []
        edge_vkeys = []
        while len(edge_vkeys) < len(edge_line_pairs.keys()):
            _, all_edge_lines, edge_vkeys = self.get_edge_lines(edge_line_pairs, None, all_edge_lines, edge_vkeys)

        all_edge_lines = [els for els in all_edge_lines if len(els) > 4]

        for n, edge_lines in enumerate(all_edge_lines):
            logger.info(
                "-- %s: 検出エッジ: %s", material_name, [f"{ekey}:{virtual_vertices[ekey].vidxs()}" for ekey in edge_lines]
            )

        logger.info("%s: エッジの抽出", material_name)

        horizonal_edge_lines = []
        vertical_edge_lines = []
        for n, edge_lines in enumerate(all_edge_lines):
            if 1 < len(all_edge_lines):
                horizonal_edge_lines.append([])
                vertical_edge_lines.append([])

            edge_dots = []
            direction_dots = []
            for prev_edge_key, now_edge_key, next_edge_key in zip(
                list(edge_lines[-1:]) + list(edge_lines[:-1]), edge_lines, list(edge_lines[1:]) + list(edge_lines[:1])
            ):
                prev_edge_pos = virtual_vertices[prev_edge_key].position()
                now_edge_pos = virtual_vertices[now_edge_key].position()
                next_edge_pos = virtual_vertices[next_edge_key].position()

                edge_dots.append(
                    MVector3D.dotProduct(
                        (now_edge_pos - prev_edge_pos).normalized(), (next_edge_pos - now_edge_pos).normalized()
                    )
                )
                direction_dots.append(
                    MVector3D.dotProduct((now_edge_pos - prev_edge_pos).normalized(), base_vertical_axis)
                )

            # 方向の中央値
            # direction_dot_mean = np.mean([np.min(np.abs(direction_dots)), np.mean(np.abs(direction_dots))])
            direction_dot_mean = np.mean(np.abs(direction_dots))
            # 内積の前後差
            edge_dot_diffs = np.diff(edge_dots)
            edge_dot_diff_max = np.max(np.abs(edge_dot_diffs))

            logger.debug(
                f"[{n:02d}] direction[{np.round(direction_dot_mean, 4)}], dot[{np.round(direction_dots, 4)}], edge_dot_diff_max[{round(edge_dot_diff_max, 4)}]"
            )

            if edge_dot_diff_max > 0.5:
                # 内積差が大きい場合、エッジが分断されてるとみなす
                logger.debug(f"[{n:02d}] corner[{np.where(np.array(edge_dots) < 0.5)[0].tolist()}]")
                slice_idxs = np.where(np.array(edge_dots) < 0.5)[0].tolist()
                slice_idxs += [slice_idxs[0]]
                # is_prev_horizonal = True
                for ssi, esi in zip(slice_idxs, slice_idxs[1:]):
                    target_edge_lines = (
                        edge_lines[ssi : (esi + 1)] if 0 <= ssi < esi else edge_lines[ssi:] + edge_lines[: (esi + 1)]
                    )
                    target_direction_dots = (
                        direction_dots[(ssi + 1) : (esi + 1)]
                        if 0 <= ssi < esi
                        else direction_dots[(ssi + 1) :] + direction_dots[: (esi + 1)]
                    )

                    if np.round(np.mean(np.abs(target_direction_dots)), 3) <= np.round(direction_dot_mean, 3):
                        # 同一方向の傾きがdirectionと垂直っぽければ、水平方向
                        if 1 == len(all_edge_lines):
                            horizonal_edge_lines.append([])
                        horizonal_edge_lines[-1].append(target_edge_lines)
                        # is_prev_horizonal = True
                    else:
                        # 同一方向の傾きがdirectionと同じっぽければ、垂直方向

                        # # 垂直が2回続いている場合、スリットとみなして、切替の一点を水平に入れておく
                        # if not is_prev_horizonal:
                        #     if 1 == len(all_edge_lines):
                        #         horizonal_edge_lines.append([])
                        #     horizonal_edge_lines[-1].append([target_edge_lines[0]])

                        if 1 == len(all_edge_lines):
                            vertical_edge_lines.append([])
                        vertical_edge_lines[-1].append(target_edge_lines)
                        # is_prev_horizonal = False

            else:
                # 内積差が小さい場合、エッジが均一に水平に繋がってるとみなす(一枚物は有り得ない)
                horizonal_edge_lines[-1].append(edge_lines)

        logger.debug(f"horizonal[{horizonal_edge_lines}]")
        logger.debug(f"vertical[{vertical_edge_lines}]")

        logger.info("%s: 水平エッジの上下判定", material_name)

        # 親ボーンとの距離
        horizonal_distances = []
        for edge_lines in horizonal_edge_lines:
            line_horizonal_distances = []
            for edge_line in edge_lines:
                horizonal_poses = []
                for edge_key in edge_line:
                    horizonal_poses.append(virtual_vertices[edge_key].position().data())
                line_horizonal_distances.append(
                    np.mean(
                        np.linalg.norm(np.array(horizonal_poses) - parent_bone.position.data(), ord=2, axis=1), axis=0
                    )
                )
            horizonal_distances.append(np.mean(line_horizonal_distances))

        # 水平方向を上下に分ける
        horizonal_total_mean_distance = np.mean(horizonal_distances)
        logger.debug(f"distance[{horizonal_total_mean_distance}], [{horizonal_distances}]")

        bottom_horizonal_edge_lines = []
        top_horizonal_edge_lines = []
        for n, (hd, hel) in enumerate(zip(horizonal_distances, horizonal_edge_lines)):
            if hd > horizonal_total_mean_distance:
                # 遠い方が下(BOTTOM)
                # 一枚物は反転
                if 1 == len(all_edge_lines):
                    bottom_horizonal_edge_lines.append([])
                    for he in hel:
                        bottom_horizonal_edge_lines[-1].insert(0, list(reversed(he)))
                else:
                    bottom_horizonal_edge_lines.append(hel)
                logger.debug(f"[{n:02d}-horizonal-bottom] {hel}")
            else:
                # 近い方が上(TOP)
                top_horizonal_edge_lines.append(hel)
                logger.debug(f"[{n:02d}-horizonal-top] {hel}")

        if not top_horizonal_edge_lines:
            logger.warning("物理方向に対して水平な上部エッジが見つけられなかった為、処理を終了します。", decoration=MLogger.DECORATION_BOX)
            return None, None, None, None

        top_keys = []
        top_degrees = {}
        top_edge_poses = []
        for ti, thel in enumerate(top_horizonal_edge_lines):
            for hi, the in enumerate(thel):
                for ei, thkey in enumerate(the):
                    top_edge_poses.append(virtual_vertices[thkey].position().data())

        top_edge_mean_pos = MVector3D(np.mean(top_edge_poses, axis=0))
        # 真後ろに最も近い位置
        top_edge_start_pos = MVector3D(list(sorted(top_edge_poses, key=lambda x: (abs(x[0]), -x[2], -x[1])))[0])

        for ti, thel in enumerate(top_horizonal_edge_lines):
            for hi, the in enumerate(thel):
                top_keys.extend(the)
                for ei, thkey in enumerate(the):
                    top_degrees[thkey] = self.calc_arc_degree(
                        top_edge_start_pos, top_edge_mean_pos, virtual_vertices[thkey].position(), base_vertical_axis
                    )
                    logger.info(
                        "%s: 水平エッジ上部(%s-%s-%s): %s -> %s",
                        material_name,
                        f"{(ti + 1):04d}",
                        f"{(hi + 1):03d}",
                        f"{(ei + 1):03d}",
                        virtual_vertices[thkey].vidxs(),
                        round(top_degrees[thkey], 3),
                    )

        if not bottom_horizonal_edge_lines:
            logger.warning("物理方向に対して水平な下部エッジが見つけられなかった為、処理を終了します。", decoration=MLogger.DECORATION_BOX)
            return None, None, None, None

        logger.info("--------------")
        bottom_keys = []
        bottom_degrees = {}
        bottom_edge_poses = []
        for bi, bhel in enumerate(bottom_horizonal_edge_lines):
            for hi, bhe in enumerate(bhel):
                for ei, bhkey in enumerate(bhe):
                    bottom_edge_poses.append(virtual_vertices[bhkey].position().data())

        bottom_edge_mean_pos = MVector3D(np.mean(bottom_edge_poses, axis=0))
        bottom_edge_start_pos = MVector3D(list(sorted(bottom_edge_poses, key=lambda x: (abs(x[0]), -x[2], -x[1])))[0])

        for bi, bhel in enumerate(bottom_horizonal_edge_lines):
            for hi, bhe in enumerate(bhel):
                bottom_keys.extend(bhe)
                for ei, bhkey in enumerate(bhe):
                    bottom_degrees[bhkey] = self.calc_arc_degree(
                        bottom_edge_start_pos,
                        bottom_edge_mean_pos,
                        virtual_vertices[bhkey].position(),
                        base_vertical_axis,
                    )
                    logger.info(
                        "%s: 水平エッジ下部(%s-%s-%s): %s -> %s",
                        material_name,
                        f"{(bi + 1):04d}",
                        f"{(hi + 1):03d}",
                        f"{(ei + 1):03d}",
                        virtual_vertices[bhkey].vidxs(),
                        round(bottom_degrees[bhkey], 3),
                    )

        logger.info("--------------------------")
        all_vkeys_list = []
        all_scores = []
        for bi, bhel in enumerate(bottom_horizonal_edge_lines):
            all_vkeys_list.append([])
            all_scores.append([])
            for hi, bhe in enumerate(bhel):
                if hi > 0:
                    all_vkeys_list.append([])
                    all_scores.append([])
                for ki, bottom_edge_key in enumerate(bhe):
                    # if len(top_degrees) == len(bottom_degrees):
                    #     # 同じ列数の場合、そのまま適用
                    #     top_edge_key = list(top_degrees.keys())[ki]
                    # else:
                    bottom_degree = bottom_degrees[bottom_edge_key]
                    # 近いdegreeのものを選ぶ(大体近いでOK)
                    top_idx = np.argmin(np.abs(np.array(list(top_degrees.values())) - bottom_degree))
                    top_edge_key = list(top_degrees.keys())[top_idx]

                    # # 途中の切れ目である場合、前後の中間角度で見る
                    # bottom_idx = [i for i, k in enumerate(bottom_degrees.keys()) if k == bottom_edge_key][0]
                    # if ki == len(bhe) - 1 and ((len(bhel) > 0 and hi < len(bhel) - 1) or \
                    #    (len(bottom_horizonal_edge_lines) > 0 and hi < len(bottom_horizonal_edge_lines) - 1)):
                    #     # 末端の場合、次との中間角度
                    #     bottom_degree = np.mean([bottom_degrees[bottom_edge_key], bottom_degrees[list(bottom_degrees.keys())[bottom_idx + 1]]])
                    # elif ki == 0 and ((len(bhel) > 0 and hi > 0) or (len(bottom_horizonal_edge_lines) > 0 and bi > 0)):
                    #     # 開始の場合、前との中間角度
                    #     bottom_degree = np.mean([bottom_degrees[bottom_edge_key], bottom_degrees[list(bottom_degrees.keys())[bottom_idx - 1]]])

                    logger.debug(
                        f"** start: ({bi:02d}-{hi:02d}), top[{top_edge_key}({virtual_vertices[top_edge_key].vidxs()})][{round(top_degrees[top_edge_key], 3)}], bottom[{bottom_edge_key}({virtual_vertices[bottom_edge_key].vidxs()})][{round(bottom_degrees[bottom_edge_key], 3)}]"
                    )

                    vkeys, vscores = self.create_vertex_line_map(
                        top_edge_key,
                        bottom_edge_key,
                        bottom_edge_key,
                        virtual_vertices,
                        top_keys,
                        bottom_keys,
                        base_vertical_axis,
                        [bottom_edge_key],
                        [1],
                    )
                    logger.info(
                        "頂点ルート走査[%s-%s-%s]: 終端: %s -> 始端: %s, スコア: %s",
                        f"{(bi + 1):04d}",
                        f"{(hi + 1):03d}",
                        f"{(ki + 1):03d}",
                        virtual_vertices[vkeys[-1]].vidxs(),
                        virtual_vertices[vkeys[0]].vidxs() if vkeys else "NG",
                        round(np.sum(vscores), 4) if vscores else "-",
                    )
                    if len(vkeys) > 1:
                        all_vkeys_list[-1].append(vkeys)
                        all_scores[-1].append(vscores)

        logger.info("%s: 絶対頂点マップの生成", material_name)
        vertex_maps = []

        midx = 0
        for li, (vkeys_list, scores) in enumerate(zip(all_vkeys_list, all_scores)):
            logger.info("-- 絶対頂点マップ: %s個目: ---------", midx + 1)

            # top_keys = []
            # line_dots = []

            logger.info("-- 絶対頂点マップ[%s]: 頂点ルート決定", midx + 1)

            # top_vv = virtual_vertices[vkeys[0]]
            # bottom_vv = virtual_vertices[vkeys[-1]]
            # top_pos = top_vv.position()
            # bottom_pos = bottom_vv.position()

            # for vkeys in vkeys_list:
            #     line_dots.append([])

            #     for y, vkey in enumerate(vkeys):
            #         if y == 0:
            #             line_dots[-1].append(1)
            #             top_keys.append(vkey)
            #         elif y <= 1:
            #             continue
            #         else:
            #             prev_prev_vv = virtual_vertices[vkeys[y - 2]]
            #             prev_vv = virtual_vertices[vkeys[y - 1]]
            #             now_vv = virtual_vertices[vkey]
            #             prev_prev_pos = prev_prev_vv.position()
            #             prev_pos = prev_vv.position()
            #             now_pos = now_vv.position()
            #             prev_direction = (prev_pos - prev_prev_pos).normalized()
            #             now_direction = (now_pos - prev_pos).normalized()

            #             dot = MVector3D.dotProduct(now_direction, prev_direction)   # * now_pos.distanceToPoint(prev_pos)   # * MVector3D.dotProduct(now_direction, total_direction)   #
            #             line_dots[-1].append(dot)

            #             logger.debug(f"target top: [{virtual_vertices[vkeys[0]].vidxs()}], bottom: [{virtual_vertices[vkeys[-1]].vidxs()}], dot({y}): {round(dot, 5)}")

            #     logger.info("-- 絶対頂点マップ[%s]: 頂点ルート確認[%s] 始端: %s, 終端: %s, 近似値: %s", midx + 1, len(top_keys), top_vv.vidxs(), bottom_vv.vidxs(), round(np.mean(line_dots[-1]), 4))

            logger.debug("------------------")
            top_key_cnts = dict(Counter([vkeys[0] for vkeys in vkeys_list]))
            target_regists = [False for _ in range(len(vkeys_list))]
            if np.max(list(top_key_cnts.values())) > 1:
                # 同じ始端から2つ以上の末端に繋がっている場合
                for top_key, cnt in top_key_cnts.items():
                    total_scores = {}
                    for x, (vkeys, ss) in enumerate(zip(vkeys_list, scores)):
                        if vkeys[0] == top_key:
                            if cnt > 1:
                                # 2個以上同じ始端から出ている場合はスコアの合計を取る
                                total_scores[x] = np.sum(ss)
                                logger.debug(
                                    f"target top: [{virtual_vertices[vkeys[0]].vidxs()}], bottom: [{virtual_vertices[vkeys[-1]].vidxs()}], total: {round(total_scores[x], 3)}"
                                )
                            else:
                                # 後はそのまま登録
                                total_scores[x] = cnt
                    # 最も内積平均値が大きい列を登録対象とする
                    target_regists[list(total_scores.keys())[np.argmax(list(total_scores.values()))]] = True
            else:
                # 全部1個ずつ繋がっている場合はそのまま登録
                target_regists = [True for _ in range(len(vkeys_list))]

            logger.debug(f"target_regists: {target_regists}")

            logger.info("-- 絶対頂点マップ[%s]: マップ生成", midx + 1)

            # XYの最大と最小の抽出
            xu = np.unique([i for i, vks in enumerate(vkeys_list) if target_regists[i]])
            yu = np.unique([i for vks in vkeys_list for i, vk in enumerate(vks)])

            # 存在しない頂点INDEXで二次元配列初期化
            vertex_map = np.full((len(yu), len(xu), 3), (np.nan, np.nan, np.nan))
            vertex_display_map = np.full((len(yu), len(xu)), " None ")
            registed_vertices = []

            prev_xx = 0
            xx = 0
            for x, vkeys in enumerate(vkeys_list):
                if not target_regists[x]:
                    # 登録対象外の場合、接続仮想頂点リストにだけは追加する
                    for y, vkey in enumerate(vkeys):
                        if np.isnan(vertex_map[y, prev_xx]).any():
                            continue
                        prev_vv = virtual_vertices[tuple(np.nan_to_num(vertex_map[y, prev_xx]))]
                        vv = virtual_vertices[vkey]
                        prev_vv.connected_vvs.extend(vv.connected_vvs)
                    continue

                for y, vkey in enumerate(vkeys):
                    vv = virtual_vertices[vkey]
                    if not vv.vidxs():
                        prev_xx = xx
                        continue

                    logger.debug(f"x: {x}, y: {y}, vv: {vkey}, vidxs: {vv.vidxs()}")

                    vertex_map[y, xx] = vkey
                    vertex_display_map[y, xx] = ":".join([str(v) for v in vv.vidxs()])
                    registed_vertices.append(vkey)

                    # 登録対象の場合、残対象から削除
                    if vkey in remaining_vertices:
                        del remaining_vertices[vkey]

                    prev_xx = xx

                xx += 1
                logger.debug("-------")

            vertex_maps.append(vertex_map)

            logger.info(
                "\n".join([", ".join(vertex_display_map[vx, :]) for vx in range(vertex_display_map.shape[0])]),
                translate=False,
            )
            logger.info("-- 絶対頂点マップ: %s個目:終了 ---------", midx + 1)

            midx += 1
            logger.debug("-----------------------")

        return vertex_maps, virtual_vertices, remaining_vertices, back_vertices

    def calc_arc_degree(
        self, start_pos: MVector3D, mean_pos: MVector3D, target_pos: MVector3D, base_vertical_axis: MVector3D
    ):
        start_normal_pos = (start_pos - mean_pos).normalized()
        target_normal_pos = (target_pos - mean_pos).normalized()
        qq = MQuaternion.rotationTo(start_normal_pos, target_normal_pos)
        degree = qq.toDegreeSign(base_vertical_axis)
        if np.isclose(MVector3D.dotProduct(start_normal_pos, target_normal_pos), -1):
            # ほぼ真後ろを向いてる場合、固定で180度を入れておく
            degree = 180
        if degree < 0:
            # マイナスになった場合、360を足しておく
            degree += 360

        return degree

    def create_vertex_line_map(
        self,
        top_edge_key: tuple,
        bottom_edge_key: tuple,
        from_key: tuple,
        virtual_vertices: dict,
        top_keys: list,
        bottom_keys: list,
        base_vertical_axis: MVector3D,
        vkeys: list,
        vscores: list,
        loop=0,
    ):

        if loop > 500:
            return None, None

        from_vv = virtual_vertices[from_key]
        from_pos = from_vv.position()

        top_vv = virtual_vertices[top_edge_key]
        top_pos = top_vv.position()

        bottom_vv = virtual_vertices[bottom_edge_key]
        bottom_pos = bottom_vv.position()

        local_next_base_pos = MVector3D(1, 0, 0)

        # ボーン進行方向(x)
        top_x_pos = (top_pos - bottom_pos).normalized()
        # ボーン進行方向に対しての縦軸(y)
        top_y_pos = top_vv.normal().normalized()
        # ボーン進行方向に対しての横軸(z)
        top_z_pos = MVector3D.crossProduct(top_x_pos, top_y_pos)
        top_qq = MQuaternion.fromDirection(top_z_pos, top_y_pos)
        logger.debug(
            f" - top({top_vv.vidxs()}): x[{top_x_pos.to_log()}], y[{top_y_pos.to_log()}], z[{top_z_pos.to_log()}]"
        )

        # int_max = np.iinfo(np.int32).max
        scores = []
        for n, to_key in enumerate(from_vv.connected_vvs):
            to_vv = virtual_vertices[to_key]
            to_pos = to_vv.position()

            direction_dot = MVector3D.dotProduct(
                (from_pos - bottom_pos).normalized(), (to_pos - from_pos).normalized()
            )
            if to_key in vkeys or to_key in bottom_keys or (from_key not in bottom_keys and direction_dot <= 0):
                # 到達済み、最下層、反対方向のベクトルには行かせない
                scores.append(0)
                logger.debug(f" - get_vertical_key({n}): from[{from_vv.vidxs()}], to[{to_vv.vidxs()}], 対象外")
                continue

            # if to_key == top_edge_key:
            #     # TOPに到達するときには必ずそこに向く
            #     scores.append(int_max)
            #     logger.debug(f' - get_vertical_key({n}): from[{from_vv.vidxs()}], to[{to_vv.vidxs()}], TOP到達')
            #     continue

            # # ボーン進行方向(x)
            # to_x_pos = (to_pos - bottom_pos).normalized()
            # # ボーン進行方向に対しての縦軸(y)
            # to_y_pos = to_vv.normal(base_vertical_axis).normalized()
            # # ボーン進行方向に対しての横軸(z)
            # to_z_pos = MVector3D.crossProduct(to_x_pos, to_y_pos)
            # to_qq = MQuaternion.fromDirection(to_z_pos, to_y_pos)

            mat = MMatrix4x4()
            mat.setToIdentity()
            mat.translate(from_pos)
            mat.rotate(top_qq)

            local_next_vpos = (mat.inverted() * to_pos).normalized()

            vec_yaw1 = (local_next_base_pos * MVector3D(1, 0, 1)).normalized()
            vec_yaw2 = (local_next_vpos * MVector3D(1, 0, 1)).normalized()
            yaw_score = calc_ratio(MVector3D.dotProduct(vec_yaw1, vec_yaw2), -1, 1, 0, 1)

            vec_pitch1 = (local_next_base_pos * MVector3D(0, 1, 1)).normalized()
            vec_pitch2 = (local_next_vpos * MVector3D(0, 1, 1)).normalized()
            pitch_score = calc_ratio(MVector3D.dotProduct(vec_pitch1, vec_pitch2), -1, 1, 0, 1)

            vec_roll1 = (local_next_base_pos * MVector3D(1, 1, 0)).normalized()
            vec_roll2 = (local_next_vpos * MVector3D(1, 1, 0)).normalized()
            roll_score = calc_ratio(MVector3D.dotProduct(vec_roll1, vec_roll2), -1, 1, 0, 1)

            score = (yaw_score * 20) + pitch_score + (roll_score * 2)
            # local_dot = MVector3D.dotProduct(base_vertical_axis, local_next_vpos)
            # prev_dot = MVector3D.dotProduct((from_pos - prev_pos).normalized(), (to_pos - from_pos).normalized()) if prev_pos else 1

            scores.append(score)

            # dots.append(local_dot * prev_dot)

            logger.debug(
                f" - get_vertical_key({n}): from[{from_vv.vidxs()}], to[{to_vv.vidxs()}], local_next_vpos[{local_next_vpos.to_log()}], score: [{score}], yaw_score: {round(yaw_score, 5)}, pitch_score: {round(pitch_score, 5)}, roll_score: {round(roll_score, 5)}"
            )

            # to_degrees.append(self.calc_arc_degree(bottom_edge_start_pos, bottom_edge_mean_pos, to_pos, base_vertical_axis))
            # to_lengths.append(to_pos.distanceToPoint(top_pos))

            # logger.debug(f' - get_vertical_key({n}) : to[{to_vv.vidxs()}], pos[{to_pos.to_log()}], degree[{round(to_degrees[-1], 4)}]')

        if np.count_nonzero(scores) == 0:
            # スコアが付けられなくなったら終了
            return vkeys, vscores

        # nearest_idx = np.where(np.array(scores) == int_max)[0]
        # if len(nearest_idx) > 0:
        #     # TOP到達した場合、採用
        #     nearest_idx = nearest_idx[0]
        #     vscores.append(1)
        # else:

        # 最もスコアの高いINDEXを採用
        nearest_idx = np.argmax(scores)
        vscores.append(np.max(scores))
        vertical_key = from_vv.connected_vvs[nearest_idx]

        logger.debug(
            f"direction: from: [{virtual_vertices[from_key].vidxs()}], to: [{virtual_vertices[vertical_key].vidxs()}]"
        )

        vkeys.insert(0, vertical_key)

        if vertical_key in top_keys:
            # 上端に辿り着いたら終了
            return vkeys, vscores

        return self.create_vertex_line_map(
            top_edge_key,
            bottom_edge_key,
            vertical_key,
            virtual_vertices,
            top_keys,
            bottom_keys,
            base_vertical_axis,
            vkeys,
            vscores,
            loop + 1,
        )

    def get_edge_lines(self, edge_line_pairs: dict, start_vkey: tuple, edge_lines: list, edge_vkeys: list, loop=0):
        if len(edge_vkeys) >= len(edge_line_pairs.keys()) or loop > 500:
            return start_vkey, edge_lines, edge_vkeys

        if not start_vkey:
            # X(中央揃え) - Z(降順) - Y(降順)
            sorted_edge_line_pairs = sorted(
                list(set(edge_line_pairs.keys()) - set(edge_vkeys)), key=lambda x: (abs(x[0]), -x[2], -x[1])
            )
            start_vkey = sorted_edge_line_pairs[0]
            edge_lines.append([start_vkey])
            edge_vkeys.append(start_vkey)

        for next_vkey in sorted(edge_line_pairs[start_vkey], key=lambda x: (x[0], x[2], -x[1])):
            if next_vkey not in edge_vkeys:
                edge_lines[-1].append(next_vkey)
                edge_vkeys.append(next_vkey)
                start_vkey, edge_lines, edge_vkeys = self.get_edge_lines(
                    edge_line_pairs, next_vkey, edge_lines, edge_vkeys, loop + 1
                )

        return None, edge_lines, edge_vkeys

    def get_rigidbody(self, model: PmxModel, bone_name: str):
        if bone_name not in model.bones:
            return None

        for rigidbody in model.rigidbodies.values():
            if rigidbody.bone_index == model.bones[bone_name].index:
                return rigidbody

        return None

    def get_block_vidxs(self, v_yidx: int, v_xidx: int, regist_bones: np.ndarray, bone_connected: np.ndarray):
        max_xidx = (
            np.max(np.where(regist_bones[v_yidx, :]))
            if regist_bones[v_yidx, :].any()
            else np.max(np.where(regist_bones[: (v_yidx + 1), :])[1])
        )

        prev_xidx = 0
        if v_xidx == 0:
            if bone_connected[v_yidx, max_xidx:].any():
                # 最後が先頭と繋がっている場合(最後の有効ボーンから最初までがどこか繋がっている場合）、最後と繋ぐ
                prev_xidx = max_xidx
        else:
            # 1番目以降は、自分より前で、ボーンが登録されている最も近いの
            prev_xidx = (
                np.max(np.where(regist_bones[v_yidx, :v_xidx]))
                if regist_bones[v_yidx, :v_xidx].any()
                else np.max(np.where(regist_bones[: (v_yidx + 1), :v_xidx])[1])
            )

        next_xidx = max_xidx
        if v_xidx >= max_xidx:
            if bone_connected[v_yidx, max_xidx:].any():
                # 最後が先頭と繋がっている場合(最後の有効ボーンから最初までがどこか繋がっている場合）、先頭と繋ぐ
                next_xidx = 0
        else:
            # 1番目以降は、自分より前で、ボーンが登録されている最も近いの
            next_xidx = (
                np.min(np.where(regist_bones[v_yidx, (v_xidx + 1) :])) + (v_xidx + 1)
                if regist_bones[v_yidx, (v_xidx + 1) :].any()
                else np.min(np.where(regist_bones[: (v_yidx + 1), (v_xidx + 1) :])[1]) + (v_xidx + 1)
            )

        above_yidx = 0
        if v_yidx > 0:
            above_yidx = (
                np.max(np.where(regist_bones[:v_yidx, v_xidx]))
                if regist_bones[:v_yidx, v_xidx].any()
                else np.max(np.where(regist_bones[:v_yidx, :v_xidx])[0])
            )

        below_yidx = regist_bones.shape[0] - 1
        if v_yidx < regist_bones.shape[0] - 1:
            below_yidx = (
                np.min(np.where(regist_bones[(v_yidx + 1) :, v_xidx])) + (v_yidx + 1)
                if regist_bones[(v_yidx + 1) :, v_xidx].any()
                else np.min(np.where(regist_bones[(v_yidx + 1) :, :v_xidx])[0]) + (v_yidx + 1)
            )

        return prev_xidx, next_xidx, above_yidx, below_yidx


def calc_ratio(ratio: float, oldmin: float, oldmax: float, newmin: float, newmax: float):
    # https://qastack.jp/programming/929103/convert-a-number-range-to-another-range-maintaining-ratio
    # NewValue = (((OldValue - OldMin) * (NewMax - NewMin)) / (OldMax - OldMin)) + NewMin
    return (((ratio - oldmin) * (newmax - newmin)) / (oldmax - oldmin)) + newmin


def randomname(n) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def calc_intersect(vP0: MVector3D, vP1: MVector3D, vQ0: MVector3D, vQ1: MVector3D) -> MVector3D:
    P0 = vP0.data()
    P1 = vP1.data()
    Q0 = vQ0.data()
    Q1 = vQ1.data()

    # Direction vectors
    DP = P1 - P0
    DQ = Q1 - Q0

    # start difference vector
    PQ = Q0 - P0

    # Find values
    a = DP.dot(DP)
    b = DP.dot(DQ)
    c = DQ.dot(DQ)
    d = DP.dot(PQ)
    e = DQ.dot(PQ)

    # Find discriminant
    DD = a * c - b * b

    if np.isclose(DD, 0):
        return (vP0 + vQ0) / 2

    # Find parameters for the closest points on lines
    tt = (b * e - c * d) / DD
    uu = (a * e - b * d) / DD

    Pt = P0 + tt * DP
    Qu = Q0 + uu * DQ

    return MVector3D(Pt)


SEMI_STANDARD_BONE_NAMES = [
    "全ての親",
    "センター",
    "グルーブ",
    "腰",
    "下半身",
    "上半身",
    "上半身2",
    "上半身3",
    "首",
    "頭",
    "両目",
    "左目",
    "右目",
    "左胸",
    "左胸先",
    "右胸",
    "右胸先",
    "左肩P",
    "左肩",
    "左肩C",
    "左腕",
    "左腕捩",
    "左腕捩1",
    "左腕捩2",
    "左腕捩3",
    "左ひじ",
    "左手捩",
    "左手捩1",
    "左手捩2",
    "左手捩3",
    "左手首",
    "左親指０",
    "左親指１",
    "左親指２",
    "左親指先",
    "左人指１",
    "左人指２",
    "左人指３",
    "左人指先",
    "左中指１",
    "左中指２",
    "左中指３",
    "左中指先",
    "左薬指１",
    "左薬指２",
    "左薬指３",
    "左薬指先",
    "左小指１",
    "左小指２",
    "左小指３",
    "左小指先",
    "右肩P",
    "右肩",
    "右肩C",
    "右腕",
    "右腕捩",
    "右腕捩1",
    "右腕捩2",
    "右腕捩3",
    "右ひじ",
    "右手捩",
    "右手捩1",
    "右手捩2",
    "右手捩3",
    "右手首",
    "右親指０",
    "右親指１",
    "右親指２",
    "右親指先",
    "右人指１",
    "右人指２",
    "右人指３",
    "右人指先",
    "右中指１",
    "右中指２",
    "右中指３",
    "右中指先",
    "右薬指１",
    "右薬指２",
    "右薬指３",
    "右薬指先",
    "右小指１",
    "右小指２",
    "右小指３",
    "右小指先",
    "腰キャンセル左",
    "左足",
    "左ひざ",
    "左足首",
    "左つま先",
    "左足IK親",
    "左足ＩＫ",
    "左つま先ＩＫ",
    "腰キャンセル右",
    "右足",
    "右ひざ",
    "右足首",
    "右つま先",
    "右足IK親",
    "右足ＩＫ",
    "右つま先ＩＫ",
    "左足D",
    "左ひざD",
    "左足首D",
    "左足先EX",
    "右足D",
    "右ひざD",
    "右足首D",
    "右足先EX",
]
