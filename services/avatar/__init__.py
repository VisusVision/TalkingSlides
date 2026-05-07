"""Local offline avatar generation helpers for VISUS VidLab."""

from .pipeline import AvatarRenderRequest, preprocess_teacher_avatar_image, render_avatar_segment_local

__all__ = [
    "AvatarRenderRequest",
    "preprocess_teacher_avatar_image",
    "render_avatar_segment_local",
]
