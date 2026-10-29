package org.anastylos.capture.guidance

/**
 * Board pose estimator seam. The Wave-2 device implementation runs
 * DICT_5X5_100 ArUco detection plus board PnP against measured
 * intrinsics on a bounded ImageAnalysis worker
 * (STRATEGY_KEEP_ONLY_LATEST); this interface keeps every Android
 * and OpenCV type out of the guidance logic so reducers, cues and
 * dwell stay pure-JVM and unit-tested.
 *
 * OpenCV artifact (Wave-2 integrated): official `org.opencv:opencv`
 * 4.14.0 AAR (Apache-2.0) via Maven Central, declared in the Android
 * module catalog; ArUco lives in main-repo objdetect and calib3d PnP
 * is fully bound. License-inventory conclusion for the shipped AAR
 * stays a CONTRACT-OWNER action (THIRD_PARTY_NOTICES Maven-closure
 * review). ABI, size delta, native-load and 16 KB page alignment are
 * recorded in WAVE2-GUIDED-CAPTURE.md. No custom marker decoder.
 */
interface PoseEstimator {
    /**
     * One analysis input in solver-native buffer coordinates. The
     * device adapter copies the Y plane plus timestamp/transform
     * epoch out of the ImageProxy and closes it in `finally`; the
     * estimator never sees an ImageProxy. `rotationDegrees` is
     * metadata for overlay mapping and association only: ArUco
     * corner order is marker-intrinsic, so solving is
     * rotation-invariant in native buffer coordinates.
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

    /**
     * Active board geometry: marker IDs with side length in mm plus
     * per-marker board-frame corners. `markerCornersMm` maps a marker
     * ID to its four (x, y) corners in TL/TR/BR/BL order (z = 0);
     * see [BoardGeometry]. `dictionary` names the ArUco dictionary
     * (`DICT_5X5_100` registration boards, `DICT_6X6_250` ChArUco).
     */
    data class BoardSpec(
        val boardId: String,
        val markerSizeMm: Double,
        val markerIds: List<Int>,
        val dictionary: String = "DICT_5X5_100",
        val markerCornersMm: Map<Int, List<List<Double>>> = emptyMap(),
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

    /**
     * Null `intrinsics` selects detection-only mode (explicitly
     * uncalibrated development state): markers are reported but no
     * pose is solved and the status stays DEGRADED, never VALID.
     */
    fun estimate(input: Input, intrinsics: Intrinsics?, board: BoardSpec): Estimate

    fun close()
}
