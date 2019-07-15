"""
(*)~---------------------------------------------------------------------------
Pupil - eye tracking platform
Copyright (C) 2012-2018 Pupil Labs

Distributed under the terms of the GNU
Lesser General Public License (LGPL v3.0).
See COPYING and COPYING.LESSER for license details.
---------------------------------------------------------------------------~(*)
"""

import platform
from enum import Enum

import cv2
import numpy as np
import OpenGL.GL as gl
import pyglui
import pyglui.cygl.utils as pyglui_utils

import gl_utils
import glfw


class GUI:
    """Custom GUI functionality for visualizing and editing surfaces"""

    # We often have to reference to the normalized surface corner coordinates,
    # which are defined to always be as follows:
    norm_corners = np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32)

    def __init__(self, tracker):
        self.tracker = tracker
        self.button_click_radius = 15

        self.heatmap_mode = Heatmap_Mode.WITHIN_SURFACE
        self.show_heatmap = False
        self.show_marker_ids = False

        self._edit_surf_corners = set()
        self._edit_surf_markers = set()
        self.heatmap_textures = {}
        self.surface_windows = {}

        self.color_primary = (1.0, 0.2, 0.6)
        self.color_secondary = (0.1, 0.85, 1.0)
        self.color_tertiary = (0, 0.82, 0.4)

        self.glfont = pyglui.pyfontstash.fontstash.Context()
        self.glfont.add_font("opensans", pyglui.ui.get_opensans_font_path())
        self.glfont.set_size(23)
        self.glfont.set_color_float((0.2, 0.5, 0.9, 1.0))

    def update(self):
        if self.show_heatmap:
            for surface in self.tracker.surfaces:
                if surface.detected:
                    self._draw_heatmap(surface)

        self._draw_markers()

        if self.show_marker_ids:
            for marker in self.tracker.markers:
                self._draw_marker_id(marker)

        for surface in self.tracker.surfaces:
            if not surface.detected:
                continue

            self._draw_surface_frames(surface)

            if surface in self._edit_surf_markers:
                self._draw_marker_toggles(surface)

            if surface in self._edit_surf_corners:
                self._draw_surface_corner_handles(surface)

            # The offline surface tracker does not have the freeze feature and thus
            # never uses the frozen_scene_tex
            if self.tracker.has_freeze_feature:
                if self.tracker.freeze_scene:
                    self.surface_windows[surface].update(self.tracker.frozen_scene_tex)
                else:
                    self.surface_windows[surface].update(self.tracker.g_pool.image_tex)
            else:
                self.surface_windows[surface].update(self.tracker.g_pool.image_tex)

    def _draw_markers(self):
        color = pyglui_utils.RGBA(*self.color_secondary, 0.5)
        for marker in self.tracker.markers_unfiltered:
            hat = np.array(
                [[[0, 0], [0, 1], [0.5, 1.3], [1, 1], [1, 0], [0, 0]]], dtype=np.float32
            )
            hat = cv2.perspectiveTransform(
                hat, _get_norm_to_points_trans(marker.verts_px)
            )

            pyglui_utils.draw_polyline(hat.reshape((6, 2)), color=color)
            if (
                marker.perimeter >= self.tracker.marker_min_perimeter
                and marker.id_confidence > self.tracker.marker_min_confidence
            ):
                pyglui_utils.draw_polyline(
                    hat.reshape((6, 2)), color=color, line_type=gl.GL_POLYGON
                )

    def _draw_marker_id(self, marker):
        verts_px = np.array(marker.verts_px, dtype=np.float32)
        verts_px.shape = (4, 2)
        anchor = np.array([np.min(verts_px[:, 0]), np.max(verts_px[:, 1])])
        line_height = 16

        text_lines = [
            f"id: {marker.tag_id}",
            f"conf: {marker.id_confidence:.2f}",
        ]

        for idx, text in enumerate(text_lines):
            loc = anchor + (0, line_height * (idx+1))
            self._draw_text(loc, text, self.color_secondary)

    def _draw_surface_frames(self, surface):
        if not surface.detected:
            return

        corners, top_indicator, title_anchor, surface_edit_anchor, marker_edit_anchor = self._get_surface_anchor_points(
            surface
        )
        alpha = min(1, surface.build_up_status)

        pyglui_utils.draw_polyline(
            corners.reshape((5, 2)), color=pyglui_utils.RGBA(*self.color_primary, alpha)
        )
        pyglui_utils.draw_polyline(
            top_indicator.reshape((4, 2)),
            color=pyglui_utils.RGBA(*self.color_primary, alpha),
        )

        self._draw_surf_menu(
            surface, title_anchor, surface_edit_anchor, marker_edit_anchor
        )

    def _get_surface_anchor_points(self, surface):
        corners = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], dtype=np.float32)
        corners = surface.map_from_surf(
            corners, self.tracker.camera_model, compensate_distortion=False
        )

        top_indicator = np.array(
            [[0.3, 0.7], [0.7, 0.7], [0.5, 0.9], [0.3, 0.7]], dtype=np.float32
        )
        top_indicator = surface.map_from_surf(
            top_indicator, self.tracker.camera_model, compensate_distortion=False
        )

        title_anchor = corners.reshape((5, -1))[2]
        title_anchor = title_anchor[0], title_anchor[1] - 75

        surface_edit_anchor = title_anchor[0], title_anchor[1] + 25
        marker_edit_anchor = title_anchor[0], title_anchor[1] + 50

        return (
            corners,
            top_indicator,
            title_anchor,
            surface_edit_anchor,
            marker_edit_anchor,
        )

    def _draw_surf_menu(
        self, surface, title_anchor, surface_edit_anchor, marker_edit_anchor
    ):
        marker_detection_status = "{}   {}/{}".format(
            surface.name,
            surface.num_detected_markers,
            len(surface.registered_markers_dist),
        )
        self._draw_text(
            (title_anchor[0] + 15, title_anchor[1] + 6),
            marker_detection_status,
            self.color_secondary,
        )

        # If the surface is defined, draw menu buttons. Otherwise draw definition
        # progress.
        if surface.defined:

            self._draw_surface_menu_buttons(
                surface, surface_edit_anchor, marker_edit_anchor
            )
        else:
            self._draw_surface_definition_progress(
                surface, surface_edit_anchor, marker_edit_anchor
            )

    def _draw_surface_menu_buttons(
        self, surface, surface_edit_anchor, marker_edit_anchor
    ):
        # Buttons
        pyglui_utils.draw_points(
            [marker_edit_anchor], color=pyglui_utils.RGBA(*self.color_primary)
        )
        if surface in self._edit_surf_markers:
            pyglui_utils.draw_points(
                [marker_edit_anchor],
                size=13,
                color=pyglui_utils.RGBA(*self.color_secondary),
            )
        pyglui_utils.draw_points(
            [surface_edit_anchor], color=pyglui_utils.RGBA(*self.color_primary)
        )
        if surface in self._edit_surf_corners:
            pyglui_utils.draw_points(
                [surface_edit_anchor],
                size=13,
                color=pyglui_utils.RGBA(*self.color_secondary),
            )
        # Text
        self._draw_text(
            (surface_edit_anchor[0] + 15, surface_edit_anchor[1] + 6),
            "edit surface",
            self.color_secondary,
        )
        self._draw_text(
            (marker_edit_anchor[0] + 15, marker_edit_anchor[1] + 6),
            "add/remove markers",
            self.color_secondary,
        )

    def _draw_surface_definition_progress(
        self, surface, surface_edit_anchor, marker_edit_anchor
    ):
        progress_text = "{:.0f} %".format(surface.build_up_status * 100)
        self._draw_text(
            (surface_edit_anchor[0] + 15, surface_edit_anchor[1] + 6),
            "Learning affiliated markers...",
            self.color_secondary,
        )
        self._draw_text(
            (marker_edit_anchor[0] + 15, marker_edit_anchor[1] + 6),
            progress_text,
            self.color_secondary,
        )

    def _draw_text(self, loc, text, color):
        self.glfont.set_blur(3.9)
        self.glfont.set_color_float((0, 0, 0, 0.8))
        self.glfont.draw_text(loc[0], loc[1], text)

        self.glfont.set_blur(0.0)
        self.glfont.set_color_float(color + (0.9,))
        self.glfont.draw_text(loc[0], loc[1], text)

    def _draw_marker_toggles(self, surface):
        active_markers = []
        inactive_markers = []
        for marker in self.tracker.markers:
            if marker.perimeter < self.tracker.marker_min_perimeter:
                continue

            centroid = np.mean(marker.verts_px, axis=0)
            centroid = (centroid[0, 0], centroid[0, 1])
            if marker.uid in surface.registered_markers_dist.keys():
                active_markers.append(centroid)
            else:
                inactive_markers.append(centroid)

        pyglui_utils.draw_points(
            inactive_markers, size=20, color=pyglui_utils.RGBA(*self.color_primary, 0.8)
        )
        pyglui_utils.draw_points(
            active_markers, size=20, color=pyglui_utils.RGBA(*self.color_tertiary, 0.8)
        )

    def _draw_surface_corner_handles(self, surface):
        img_corners = surface.map_from_surf(
            self.norm_corners.copy(),
            self.tracker.camera_model,
            compensate_distortion=False,
        )

        pyglui_utils.draw_points(
            img_corners, size=20, color=pyglui_utils.RGBA(*self.color_primary, 0.5)
        )

    def _draw_heatmap(self, surface):
        # TODO The heatmap is computed in undistorted space. For the visualization to
        # be precisely correct we would have to distort the heatmap accordingly.
        if self.heatmap_mode == Heatmap_Mode.WITHIN_SURFACE:
            self.heatmap_textures[surface].update_from_ndarray(
                surface.within_surface_heatmap
            )
        else:
            self.heatmap_textures[surface].update_from_ndarray(
                surface.across_surface_heatmap
            )
        width, height = self.tracker.camera_model.resolution
        img_corners = np.array(
            [(0, height), (width, height), (width, 0), (0, 0)], dtype=np.float32
        )
        norm_trans = _get_points_to_norm_trans(img_corners)

        trans_mat = norm_trans @ surface.surf_to_dist_img_trans
        trans_mat = gl_utils.cvmat_to_glmat(trans_mat)

        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glPushMatrix()
        gl.glLoadIdentity()
        gl.glOrtho(0, 1, 0, 1, -1, 1)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glPushMatrix()
        # apply trans_mat  to our quad - this will stretch the quad such that the ref suface will span the window extends
        gl.glLoadMatrixf(trans_mat)
        self.heatmap_textures[surface].draw()
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glPopMatrix()
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glPopMatrix()

    def on_click(self, pos, button, action):
        pos = np.array(pos, dtype=np.float32)
        self._on_click_menu_buttons(action, pos)
        self._on_click_corner_handles(action, pos)
        self._on_click_marker_toggles(action, pos)

    def _on_click_menu_buttons(self, action, pos):
        if action == glfw.GLFW_PRESS:
            for surface in self.tracker.surfaces:

                if not surface.detected:
                    continue

                surface_edit_pressed, marker_edit_pressed = self._check_surface_button_pressed(
                    surface, pos
                )

                if surface_edit_pressed:
                    if surface in self._edit_surf_corners:
                        self._edit_surf_corners.remove(surface)
                    else:
                        self._edit_surf_corners.add(surface)

                if marker_edit_pressed:
                    if surface in self._edit_surf_markers:
                        self._edit_surf_markers.remove(surface)
                    else:
                        self._edit_surf_markers.add(surface)

    def _on_click_corner_handles(self, action, pos):
        for surface in self._edit_surf_corners:
            if surface.detected and surface.defined:
                img_corners = surface.map_from_surf(
                    self.norm_corners.copy(),
                    self.tracker.camera_model,
                    compensate_distortion=False,
                )
                for idx, corner in enumerate(img_corners):
                    dist = np.linalg.norm(corner - pos)
                    if dist < self.button_click_radius:
                        if action == glfw.GLFW_PRESS:
                            self.tracker._edit_surf_verts.append((surface, idx))
                        elif action == glfw.GLFW_RELEASE:
                            self.tracker.notify_all(
                                {
                                    "subject": "surface_tracker.surfaces_changed",
                                    "name": surface.name,
                                }
                            )
                            self.tracker._edit_surf_verts = []

    def _on_click_marker_toggles(self, action, pos):
        if action == glfw.GLFW_PRESS:
            for surface in self._edit_surf_markers:
                if not surface.detected:
                    continue
                for marker in self.tracker.markers:
                    centroid = np.mean(marker.verts_px, axis=0)
                    dist = np.linalg.norm(centroid - pos)
                    if dist < self.button_click_radius:
                        if marker.uid not in surface.registered_markers_dist.keys():
                            surface.add_marker(
                                marker.uid, marker.verts_px, self.tracker.camera_model
                            )
                        else:
                            surface.pop_marker(marker.uid)
                        self.tracker.notify_all(
                            {
                                "subject": "surface_tracker.surfaces_changed",
                                "name": surface.name,
                            }
                        )

    def _check_surface_button_pressed(self, surface, pos):
        (
            corners,
            top_indicator,
            title_anchor,
            surface_edit_anchor,
            marker_edit_anchor,
        ) = self._get_surface_anchor_points(surface)

        surface_edit_anchor = np.array(surface_edit_anchor)
        marker_edit_anchor = np.array(marker_edit_anchor)

        dist_surface_edit = np.linalg.norm(pos - surface_edit_anchor)
        surface_edit_pressed = dist_surface_edit < self.button_click_radius

        dist_marker_edit = np.linalg.norm(pos - marker_edit_anchor)
        marker_edit_pressed = dist_marker_edit < self.button_click_radius

        return surface_edit_pressed, marker_edit_pressed

    def add_surface(self, surface):
        self.heatmap_textures[surface] = pyglui_utils.Named_Texture()
        self.surface_windows[surface] = Surface_Window(surface, self.tracker)

    def remove_surface(self, surface):
        self.heatmap_textures.pop(surface)
        self._edit_surf_markers.discard(surface)
        self._edit_surf_corners.discard(surface)
        self.surface_windows.pop(surface)


def _get_norm_to_points_trans(points):
    norm_corners = np.array(((0, 0), (1, 0), (1, 1), (0, 1)), dtype=np.float32)
    return cv2.getPerspectiveTransform(norm_corners, np.array(points, dtype=np.float32))


def _get_points_to_norm_trans(points):
    norm_corners = np.array(((0, 0), (1, 0), (1, 1), (0, 1)), dtype=np.float32)
    return cv2.getPerspectiveTransform(np.array(points, dtype=np.float32), norm_corners)


class Surface_Window:
    def __init__(self, surface, tracker):
        self.surface = surface
        self._window = None
        self.window_should_open = False
        self.window_should_close = False
        self.tracker = tracker

        self.trackball = None
        self.input = None

        # UI Platform tweaks
        if platform.system() == "Linux":
            self.window_position_default = (0, 0)
        elif platform.system() == "Windows":
            self.window_position_default = (8, 90)
        else:
            self.window_position_default = (0, 0)

    def open_close_window(self):
        if self._window:
            self.close_window()
        else:
            self.open_window()

    def open_window(self):
        if not self._window:

            monitor = None
            # open with same aspect ratio as surface
            surface_aspect_ratio = (
                self.surface.real_world_size["x"] / self.surface.real_world_size["y"]
            )
            win_h = 640
            win_w = int(win_h / surface_aspect_ratio)

            self._window = glfw.glfwCreateWindow(
                win_h,
                win_w,
                "Reference Surface: " + self.surface.name,
                monitor=monitor,
                share=glfw.glfwGetCurrentContext(),
            )

            glfw.glfwSetWindowPos(
                self._window,
                self.window_position_default[0],
                self.window_position_default[1],
            )

            self.trackball = gl_utils.trackball.Trackball()
            self.input = {"down": False, "mouse": (0, 0)}

            # Register callbacks
            glfw.glfwSetFramebufferSizeCallback(self._window, self.on_resize)
            glfw.glfwSetKeyCallback(self._window, self.on_window_key)
            glfw.glfwSetWindowCloseCallback(self._window, self.on_close)
            glfw.glfwSetMouseButtonCallback(self._window, self.on_window_mouse_button)
            glfw.glfwSetCursorPosCallback(self._window, self.on_pos)
            glfw.glfwSetScrollCallback(self._window, self.on_scroll)

            self.on_resize(self._window, *glfw.glfwGetFramebufferSize(self._window))

            # gl_state settings
            active_window = glfw.glfwGetCurrentContext()
            glfw.glfwMakeContextCurrent(self._window)
            gl_utils.basic_gl_setup()
            gl_utils.make_coord_system_norm_based()

            # refresh speed settings
            glfw.glfwSwapInterval(0)

            glfw.glfwMakeContextCurrent(active_window)

    def close_window(self):
        if self._window:
            glfw.glfwDestroyWindow(self._window)
            self._window = None
            self.window_should_close = False

    def update(self, tex):
        self.gl_display_in_window(tex)

    def gl_display_in_window(self, world_tex):
        """
        here we map a selected surface onto a separate window.
        """
        if self._window and self.surface.detected:
            active_window = glfw.glfwGetCurrentContext()
            glfw.glfwMakeContextCurrent(self._window)
            gl_utils.clear_gl_screen()

            # cv uses 3x3 gl uses 4x4 transformation matrices
            width, height = self.tracker.camera_model.resolution
            img_corners = np.array(
                [(0, height), (width, height), (width, 0), (0, 0)], dtype=np.float32
            )
            denorm_trans = _get_norm_to_points_trans(img_corners)

            trans_mat = self.surface.dist_img_to_surf_trans @ denorm_trans
            trans_mat = gl_utils.cvmat_to_glmat(trans_mat)

            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glPushMatrix()
            gl.glLoadIdentity()
            gl.glOrtho(0, 1, 0, 1, -1, 1)
            gl.glMatrixMode(gl.GL_MODELVIEW)
            gl.glPushMatrix()
            # apply trans_mat to our quad - this will stretch the quad such that the
            # surface will span the window extends
            gl.glLoadMatrixf(trans_mat)

            world_tex.draw()

            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glPopMatrix()
            gl.glMatrixMode(gl.GL_MODELVIEW)
            gl.glPopMatrix()

            self.draw_recent_pupil_positions()

            glfw.glfwSwapBuffers(self._window)
            glfw.glfwMakeContextCurrent(active_window)

    def draw_recent_pupil_positions(self):
        try:
            for gp in self.surface.gaze_history:
                pyglui_utils.draw_points(
                    [gp["norm_pos"]],
                    color=pyglui_utils.RGBA(0.0, 0.8, 0.5, 0.8),
                    size=80,
                )
        except AttributeError:
            # If gaze_history does not exist, we are in the Surface_Tracker_Offline.
            # In this case gaze visualizations will be drawn directly onto the scene
            # image and thus propagate to the surface crop automatically.
            pass

    def on_resize(self, window, w, h):
        self.trackball.set_window_size(w, h)
        active_window = glfw.glfwGetCurrentContext()
        glfw.glfwMakeContextCurrent(window)
        gl_utils.adjust_gl_view(w, h)
        glfw.glfwMakeContextCurrent(active_window)

    def on_window_key(self, window, key, scancode, action, mods):
        if action == glfw.GLFW_PRESS:
            if key == glfw.GLFW_KEY_ESCAPE:
                self.on_close()

    def on_close(self, window=None):
        self.close_window()

    def on_window_mouse_button(self, window, button, action, mods):
        if action == glfw.GLFW_PRESS:
            self.input["down"] = True
            self.input["mouse"] = glfw.glfwGetCursorPos(window)
        if action == glfw.GLFW_RELEASE:
            self.input["down"] = False

    def on_pos(self, window, x, y):
        if self.input["down"]:
            old_x, old_y = self.input["mouse"]
            self.trackball.drag_to(x - old_x, y - old_y)
            self.input["mouse"] = x, y

    def on_scroll(self, window, x, y):
        self.trackball.zoom_to(y)


class Heatmap_Mode(Enum):
    WITHIN_SURFACE = "Gaze within each surface"
    ACROSS_SURFACES = "Gaze across different surfaces"
