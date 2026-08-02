from avatar.digital_twin.hardware import GpuSnapshot, apply_local_inference_profile, select_inference_profile


def test_8gb_laptop_profile_uses_sequential_fp16_small_batches():
    profile = select_inference_profile(
        GpuSnapshot(name="NVIDIA GeForce RTX 4060 Laptop GPU", total_mib=8188, free_mib=4251)
    )
    assert profile.name == "ada_laptop_8gb"
    assert profile.defaults["MUSETALK_USE_FLOAT16"] == "1"
    assert profile.defaults["MUSETALK_BATCH_SIZE"] == "2"
    assert profile.defaults["DIGITAL_TWIN_MAX_PARALLEL_GPU_JOBS"] == "1"
    assert profile.defaults["DIGITAL_TWIN_DELIVERY_MAX_HEIGHT"] == "720"
    assert "close_gpu_apps_before_render" in profile.warnings


def test_explicit_operator_values_are_not_overwritten():
    environ = {"MUSETALK_BATCH_SIZE": "1", "DIGITAL_TWIN_DELIVERY_MAX_HEIGHT": "540"}
    profile = apply_local_inference_profile(
        environ,
        gpu=GpuSnapshot(name="RTX 4060", total_mib=8188, free_mib=7000),
    )
    assert profile.name == "ada_laptop_8gb"
    assert environ["MUSETALK_BATCH_SIZE"] == "1"
    assert environ["DIGITAL_TWIN_DELIVERY_MAX_HEIGHT"] == "540"
    assert environ["MUSETALK_USE_FLOAT16"] == "1"


def test_24gb_profile_keeps_higher_inference_batch():
    profile = select_inference_profile(GpuSnapshot(name="RTX 4090", total_mib=24564, free_mib=22000))
    assert profile.name == "workstation_24gb_plus"
    assert profile.defaults["MUSETALK_BATCH_SIZE"] == "8"
    assert profile.defaults["DIGITAL_TWIN_DELIVERY_MAX_HEIGHT"] == "1080"
