package org.anastylos.capture.guidance

import org.opencv.android.OpenCVLoader
import org.opencv.calib3d.Calib3d
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.MatOfDouble
import org.opencv.core.MatOfPoint2f
import org.opencv.core.MatOfPoint3f
import org.opencv.objdetect.ArucoDetector
import org.opencv.objdetect.DetectorParameters
import org.opencv.objdetect.Objdetect
import org.opencv.core.Point3
import org.opencv.core.Point

/**
 * Device pose estimator: ArUco detection plus board PnP through the
 * official OpenCV AAR. Native calls live behind [ArucoBackend] so the
 * glue (validation, gates, status mapping, exception containment) is
 * unit-tested with a fake; only [OpenCvArucoBackend] touches Mat.
 *
 * Status contract: VALID carries a gated metric pose; AMBIGUOUS
 * carries a best-effort pose that must never arm a shutter;
 * DEGRADED/LOST/BAD_CALIBRATION carry no pose. Any backend
 * exception degrades to LOST (no markers usable) — the analyzer
 * keeps running and the UI reports tracking loss.
 */
class OpenCvPoseEstimator(
    private val backend: ArucoBackend = OpenCvArucoBackend(),
) : PoseEstimator {

    /** The two native operations, isolated for unit testing. */
    interface ArucoBackend {
        fun detect(
            gray: ByteArray,
            width: Int,
            height: Int,
            dictionary: Int,
        ): List<PoseMath.MarkerDetection>

        data class PnpSolution(val rvec: List<Double>, val tvec: List<Double>)

        /** Null when the solver declines; throws only on misuse. */
        fun solve(
            objectMm: List<List<Double>>,
            imagePx: List<List<Double>>,
            intrinsics: PoseEstimator.Intrinsics,
        ): PnpSolution?

        fun release()
    }

    override fun estimate(
        input: PoseEstimator.Input,
        intrinsics: PoseEstimator.Intrinsics?,
        board: PoseEstimator.BoardSpec,
    ): PoseEstimator.Estimate {
        val dictionaryId = when (board.dictionary) {
            "DICT_5X5_100" -> Objdetect.DICT_5X5_100
            "DICT_6X6_250" -> Objdetect.DICT_6X6_250
            else -> null
        }
        val quality = try {
            if (input.yPlane.size == input.width * input.height) {
                FrameQuality.measure(input.yPlane, input.width, input.height)
            } else {
                null
            }
        } catch (_: Exception) {
            null
        }
        if (dictionaryId == null || input.yPlane.size != input.width * input.height) {
            return lost(input, quality)
        }
        val detections = try {
            backend.detect(input.yPlane, input.width, input.height, dictionaryId)
        } catch (_: LinkageError) {
            return lost(input, quality)
        } catch (_: Exception) {
            return lost(input, quality)
        }
        if (detections.isEmpty()) return lost(input, quality)
        val seen = detections.map { it.id }
        val corners = detections.sumOf { it.corners.size }
        val cornersPx = detections.map { PoseEstimator.MarkerCorners(it.id, it.corners) }
        val correspondences = PoseMath.assembleCorrespondences(detections, board)
        if (correspondences.size < 4 || intrinsics == null) {
            return degraded(input, seen, corners, cornersPx, quality)
        }
        if (!saneIntrinsics(intrinsics, input)) {
            return PoseEstimator.Estimate(
                tBC = null, markerIdsSeen = seen, cornerCount = corners,
                rmsPx = null, status = WireRegistration.BAD_CALIBRATION,
                sensorTimestampNs = input.sensorTimestampNs, monotonicNs = input.monotonicNs,
                cornersPx = cornersPx, frameQuality = quality,
            )
        }
        val solution = try {
            backend.solve(
                correspondences.map { it.objectMm },
                correspondences.map { it.imagePx },
                intrinsics,
            )
        } catch (_: LinkageError) {
            null
        } catch (_: Exception) {
            null
        } ?: return degraded(input, seen, corners, cornersPx, quality)
        return qualify(input, seen, corners, cornersPx, quality, correspondences, solution, intrinsics)
    }

    private fun lost(
        input: PoseEstimator.Input,
        quality: FrameQuality.Measurement?,
    ) = PoseEstimator.Estimate(
        tBC = null, markerIdsSeen = emptyList(), cornerCount = 0,
        rmsPx = null, status = WireRegistration.LOST,
        sensorTimestampNs = input.sensorTimestampNs, monotonicNs = input.monotonicNs,
        frameQuality = quality,
    )

    private fun degraded(
        input: PoseEstimator.Input,
        seen: List<Int>,
        corners: Int,
        cornersPx: List<PoseEstimator.MarkerCorners>,
        quality: FrameQuality.Measurement?,
    ) = PoseEstimator.Estimate(
        tBC = null, markerIdsSeen = seen, cornerCount = corners,
        rmsPx = null, status = WireRegistration.DEGRADED,
        sensorTimestampNs = input.sensorTimestampNs, monotonicNs = input.monotonicNs,
        cornersPx = cornersPx, frameQuality = quality,
    )

    private fun qualify(
        input: PoseEstimator.Input,
        seen: List<Int>,
        corners: Int,
        cornersPx: List<PoseEstimator.MarkerCorners>,
        quality: FrameQuality.Measurement?,
        correspondences: List<PoseMath.Correspondence>,
        solution: ArucoBackend.PnpSolution,
        intrinsics: PoseEstimator.Intrinsics,
    ): PoseEstimator.Estimate {
        val tBC = PoseMath.composeTBC(solution.rvec, solution.tvec)
        val rotation = rotationOfRowMajor(tBC)
        val center = translationOfRowMajor(tBC)
        val rotationOk = PoseMath.isValidRotation(rotation)
        val inFront = center.z < 0.0
        val rms = PoseMath.reprojectionRms(correspondences, solution.rvec, solution.tvec, intrinsics)
        val maxResidual = maxResidualPx(correspondences, solution, intrinsics)
        val spread = PoseMath.cornerSpread(
            correspondences.map { it.imagePx }, input.width, input.height,
        )
        val scale = input.width.toDouble() / GuidanceHypothesis1.REF_WIDTH_PX
        val knownMarkers = correspondences.size / 4
        val gated = rotationOk && inFront && rms != null && maxResidual != null &&
            knownMarkers >= GuidanceHypothesis1.MIN_MARKERS &&
            corners >= GuidanceHypothesis1.MIN_CORNERS &&
            rms <= GuidanceHypothesis1.LIVE_RMS_PX * scale &&
            maxResidual <= GuidanceHypothesis1.LIVE_MAX_PX * scale &&
            spread.fracW >= GuidanceHypothesis1.CORNER_SPREAD_FRAC &&
            spread.fracH >= GuidanceHypothesis1.CORNER_SPREAD_FRAC &&
            spread.hullAreaFrac >= GuidanceHypothesis1.HULL_AREA_FRAC
        // An ambiguous pose may still carry a best-effort T for coarse
        // cues, but only a geometrically sane one; anything mirrored
        // or behind the board is withheld entirely.
        val pose = if (rotationOk && inFront) tBC else null
        return PoseEstimator.Estimate(
            tBC = if (gated) tBC else pose,
            markerIdsSeen = seen, cornerCount = corners, rmsPx = rms,
            status = if (gated) WireRegistration.VALID else WireRegistration.AMBIGUOUS,
            sensorTimestampNs = input.sensorTimestampNs, monotonicNs = input.monotonicNs,
            cornersPx = cornersPx, frameQuality = quality,
        )
    }

    private fun maxResidualPx(
        correspondences: List<PoseMath.Correspondence>,
        solution: ArucoBackend.PnpSolution,
        intrinsics: PoseEstimator.Intrinsics,
    ): Double? {
        val projected = PoseMath.projectPoints(
            correspondences.map { it.objectMm }, solution.rvec, solution.tvec,
            intrinsics.fx, intrinsics.fy, intrinsics.cx, intrinsics.cy,
            intrinsics.distortion,
        )
        var max = 0.0
        for (index in correspondences.indices) {
            val prediction = projected[index] ?: return null
            val observed = correspondences[index].imagePx
            val dx = prediction[0] - observed[0]
            val dy = prediction[1] - observed[1]
            val residual = kotlin.math.sqrt(dx * dx + dy * dy)
            if (residual > max) max = residual
        }
        return max
    }

    private fun saneIntrinsics(intrinsics: PoseEstimator.Intrinsics, input: PoseEstimator.Input): Boolean {
        if (intrinsics.fx <= 0.0 || intrinsics.fy <= 0.0) return false
        if (intrinsics.cx < 0.0 || intrinsics.cx >= input.width) return false
        if (intrinsics.cy < 0.0 || intrinsics.cy >= input.height) return false
        if (intrinsics.width != input.width || intrinsics.height != input.height) return false
        if (intrinsics.distortion.size !in setOf(0, 4, 5, 8)) return false
        if (intrinsics.distortion.any { !it.isFinite() }) return false
        if (intrinsics.profileId.isBlank()) return false
        return true
    }

    override fun close() {
        try {
            backend.release()
        } catch (_: Exception) {
        }
    }
}

/**
 * Production OpenCV backend. Loads the native library lazily on
 * first use (`OpenCVLoader.initLocal()` reads `libopencv_java4.so`
 * from the APK; no OpenCV Manager). Every Mat is released in
 * `finally`; detector instances are reused across frames.
 */
class OpenCvArucoBackend : OpenCvPoseEstimator.ArucoBackend {
    private val detectors = mutableMapOf<Int, ArucoDetector>()
    private var released = false

    @Volatile private var loadFailed = false

    /**
     * Loads `libopencv_java4.so` before any Mat exists. Failure
     * latches: a missing native library degrades every frame to
     * LOST (via the estimator's containment) instead of retrying
     * the load — and its log spam — at frame rate.
     */
    private fun ensureLoaded() {
        check(!released) { "backend released" }
        if (loadFailed) throw IllegalStateException("OpenCV native init failed")
        val ok = try {
            OpenCVLoader.initLocal()
        } catch (_: LinkageError) {
            false
        } catch (_: Exception) {
            // initLocal may explode instead of returning false
            // (missing loader plumbing); either way native is out.
            false
        }
        if (!ok) {
            loadFailed = true
            throw IllegalStateException("OpenCV native init failed")
        }
    }

    private fun detectorFor(dictionary: Int): ArucoDetector {
        check(!released) { "backend released" }
        return detectors.getOrPut(dictionary) {
            ensureLoaded()
            val dictionaryMat = Objdetect.getPredefinedDictionary(dictionary)
            ArucoDetector(dictionaryMat, DetectorParameters())
        }
    }

    override fun detect(
        gray: ByteArray,
        width: Int,
        height: Int,
        dictionary: Int,
    ): List<PoseMath.MarkerDetection> {
        ensureLoaded()
        val image = Mat(height, width, CvType.CV_8UC1)
        val corners: MutableList<Mat> = mutableListOf()
        val ids = Mat()
        try {
            image.put(0, 0, gray)
            detectorFor(dictionary).detectMarkers(image, corners, ids)
            if (ids.empty()) return emptyList()
            val out = mutableListOf<PoseMath.MarkerDetection>()
            for (marker in 0 until ids.rows()) {
                val id = ids.get(marker, 0)[0].toInt()
                val quad = corners[marker]
                val points = (0..3).map { corner ->
                    val point = quad.get(corner, 0)
                    listOf(point[0], point[1])
                }
                out += PoseMath.MarkerDetection(id, points)
            }
            return out
        } finally {
            image.release()
            ids.release()
            for (corner in corners) corner.release()
        }
    }

    override fun solve(
        objectMm: List<List<Double>>,
        imagePx: List<List<Double>>,
        intrinsics: PoseEstimator.Intrinsics,
    ): OpenCvPoseEstimator.ArucoBackend.PnpSolution? {
        require(objectMm.size == imagePx.size && objectMm.size >= 4) { "need >= 4 correspondences" }
        ensureLoaded()
        val objectPoints = MatOfPoint3f()
        val imagePoints = MatOfPoint2f()
        val cameraMatrix = Mat.zeros(3, 3, CvType.CV_64F)
        val distCoeffs = MatOfDouble()
        val rvec = Mat()
        val tvec = Mat()
        try {
            objectPoints.fromList(objectMm.map { Point3(it[0], it[1], it[2]) })
            imagePoints.fromList(imagePx.map { Point(it[0], it[1]) })
            cameraMatrix.put(0, 0, intrinsics.fx, 0.0, intrinsics.cx)
            cameraMatrix.put(1, 0, 0.0, intrinsics.fy, intrinsics.cy)
            cameraMatrix.put(2, 0, 0.0, 0.0, 1.0)
            val distortion = intrinsics.distortion.ifEmpty { listOf(0.0, 0.0, 0.0, 0.0, 0.0) }
            distCoeffs.fromList(distortion)
            val ok = Calib3d.solvePnP(
                objectPoints, imagePoints, cameraMatrix, distCoeffs,
                rvec, tvec, false, Calib3d.SOLVEPNP_SQPNP,
            )
            if (!ok) return null
            return OpenCvPoseEstimator.ArucoBackend.PnpSolution(
                rvec = listOf(rvec.get(0, 0)[0], rvec.get(1, 0)[0], rvec.get(2, 0)[0]),
                tvec = listOf(tvec.get(0, 0)[0], tvec.get(1, 0)[0], tvec.get(2, 0)[0]),
            )
        } finally {
            objectPoints.release()
            imagePoints.release()
            cameraMatrix.release()
            distCoeffs.release()
            rvec.release()
            tvec.release()
        }
    }

    override fun release() {
        released = true
        detectors.clear()
    }
}
