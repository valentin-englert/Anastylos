package org.anastylos.capture.guidance

/**
 * Board pose estimator seam. The Wave-2 device implementation runs
 * DICT_5X5_100 ArUco detection plus board PnP against measured
 * intrinsics on a bounded ImageAnalysis worker
 * (STRATEGY_KEEP_ONLY_LATEST); this interface keeps every Android
 * and OpenCV type out of the guidance logic so reducers, cues and
 * dwell stay pure-JVM and unit-tested.
 *
 * OpenCV artifact decision (Wave-2 prerequisite, CONTRACT-OWNER
 * action — the version catalog is not lane-owned): official
 * `org.opencv:opencv-android` 4.x (Apache-2.0) is the recommended
 * candidate, pending the G0 dependency gate evidence (exact
 * API/ABI for arm64 PnP, AAR size delta, native-load behavior,
 * 16 KB page-size compliance at targetSdk 36+, license inventory
 * update). No custom marker decoder.
 */
interface PoseEstimator {
    /**
     * One analysis input in solver-native buffer coordinates. The
     * device adapter copies the Y plane plus timestamp/transform
     * epoch out of the ImageProxy and closes it in `finally`; the
     * estimator never sees an ImageProxy.
     */
    data class Input(
        val yPlane: ByteArray,
        val width: Int,
        val height: Int,
        val rotationDegrees: Int,
        val sensorTimestampNs: Long,
        /** Monotonic clock reading (elapsedRealtimeNanos basis). */
        val monotonicNs: Long,
        val transformEpochId: String,
    )

    /**
     * Calibrated intrinsics bound to the exact analysis stream
     * (resolution, crop, zoom, focus). Profile reuse after an
     * incompatible lens/crop/focus change is refused by the caller.
     */
    data class Intrinsics(
        val fx: Double,
        val fy: Double,
        val cx: Double,
        val cy: Double,
        val width: Int,
        val height: Int,
        /** Conventional 5-coefficient distortion, normalized coords. */
        val distortion: List<Double>,
        val profileId: String,
    )

    /** Active board geometry: marker IDs with side length in mm. */
    data class BoardSpec(
        val boardId: String,
        val markerSizeMm: Double,
        val markerIds: List<Int>,
    )

    /**
     * One pose estimate. `tBC` is row-major T_B_C (board x right, y
     * down, z into plane — preserved, never flipped). Null `tBC`
     * means no pose (see [status]); callers must not reuse a stale one.
     */
    data class Estimate(
        val tBC: List<Double>?,
        val markerIdsSeen: List<Int>,
        val cornerCount: Int,
        val rmsPx: Double?,
        val status: WireRegistration,
        val sensorTimestampNs: Long,
        val monotonicNs: Long,
    )

    fun estimate(input: Input, intrinsics: Intrinsics, board: BoardSpec): Estimate

    fun close()
}
