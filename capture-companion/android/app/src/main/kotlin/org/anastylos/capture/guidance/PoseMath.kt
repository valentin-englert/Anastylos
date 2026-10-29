package org.anastylos.capture.guidance

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Pure-JVM pose math for ArUco board tracking. No Android or OpenCV
 * types: every function here is unit-tested, including against an
 * OpenCV-Python oracle for projection, distortion and orientation
 * handling (see test resources `pnp-oracle-*.json`).
 *
 * Conventions (frozen): board frame x right, y down, z into the
 * plane; object points take z = 0; detected corners arrive TL, TR,
 * BR, BL; T_B_C is row-major with the camera center at [3,7,11]
 * (see [translationOfRowMajor]).
 */
object PoseMath {
    /** One detected marker: corners TL/TR/BR/BL in solver pixels. */
    data class MarkerDetection(
        val id: Int,
        val corners: List<List<Double>>,
    )

    /** One 3D↔2D correspondence: board-mm (x, y, 0) and pixels (u, v). */
    data class Correspondence(
        val objectMm: List<Double>,
        val imagePx: List<Double>,
    )

    /**
     * Object/image correspondences for detections with known board
     * geometry, in detection order (corners TL→BL). Detections whose
     * ID is unknown to the board, or whose corner count is not 4, are
     * skipped; the caller gates on [GuidanceHypothesis1.MIN_MARKERS].
     */
    fun assembleCorrespondences(
        detections: List<MarkerDetection>,
        board: PoseEstimator.BoardSpec,
    ): List<Correspondence> {
        val out = mutableListOf<Correspondence>()
        for (detection in detections) {
            if (detection.corners.size != 4) continue
            val model = board.markerCornersMm[detection.id] ?: continue
            if (model.size != 4) continue
            for (corner in 0..3) {
                val obj = model[corner]
                val img = detection.corners[corner]
                if (obj.size != 2 || img.size != 2) continue
                out += Correspondence(
                    objectMm = listOf(obj[0], obj[1], 0.0),
                    imagePx = listOf(img[0], img[1]),
                )
            }
        }
        return out
    }

    /**
     * Row-major 3x3 rotation from an axis-angle vector (Rodrigues),
     * matching `cv::Rodrigues` including the near-zero-angle branch.
     */
    fun rodriguesToMatrix(rvec: List<Double>): List<Double> {
        require(rvec.size == 3) { "rvec must hold 3 doubles" }
        val theta = sqrt(rvec[0] * rvec[0] + rvec[1] * rvec[1] + rvec[2] * rvec[2])
        if (theta < 1e-12) return listOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        val x = rvec[0] / theta
        val y = rvec[1] / theta
        val z = rvec[2] / theta
        val c = cos(theta)
        val s = sin(theta)
        val t = 1.0 - c
        return listOf(
            t * x * x + c, t * x * y - s * z, t * x * z + s * y,
            t * x * y + s * z, t * y * y + c, t * y * z - s * x,
            t * x * z - s * y, t * y * z + s * x, t * z * z + c,
        )
    }

    /**
     * Row-major T_B_C from an OpenCV solvePnP result. PnP returns the
     * object→camera map (x_cam = R x_obj + t); T_B_C is its inverse,
     * [R^T, -R^T t], whose translation is the camera center in the
     * board frame.
     */
    fun composeTBC(rvec: List<Double>, tvec: List<Double>): List<Double> {
        require(rvec.size == 3 && tvec.size == 3) { "rvec/tvec must hold 3 doubles" }
        val r = rodriguesToMatrix(rvec)
        // R^T (transpose of row-major 3x3).
        val rt = listOf(r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8])
        val tx = -(rt[0] * tvec[0] + rt[1] * tvec[1] + rt[2] * tvec[2])
        val ty = -(rt[3] * tvec[0] + rt[4] * tvec[1] + rt[5] * tvec[2])
        val tz = -(rt[6] * tvec[0] + rt[7] * tvec[1] + rt[8] * tvec[2])
        return listOf(
            rt[0], rt[1], rt[2], tx,
            rt[3], rt[4], rt[5], ty,
            rt[6], rt[7], rt[8], tz,
            0.0, 0.0, 0.0, 1.0,
        )
    }

    /** Determinant of a row-major 3x3 matrix. */
    fun rotationDeterminant(r: List<Double>): Double {
        require(r.size == 9) { "rotation must hold 9 row-major doubles" }
        return r[0] * (r[4] * r[8] - r[5] * r[7]) -
            r[1] * (r[3] * r[8] - r[5] * r[6]) +
            r[2] * (r[3] * r[7] - r[4] * r[6])
    }

    /**
     * Proper-rotation check: orthonormal within `tol` and determinant
     * +1 within `tol`. Mirrored (det −1) or skewed solver output is
     * rejected by the caller.
     */
    fun isValidRotation(r: List<Double>, tol: Double = 1e-6): Boolean {
        require(r.size == 9) { "rotation must hold 9 row-major doubles" }
        // NaN/Inf must fail loudly: every comparison below is false for
        // NaN, so without this guard garbage would validate as proper.
        if (r.any { !it.isFinite() }) return false
        if (abs(rotationDeterminant(r) - 1.0) > tol) return false
        for (row in 0..2) {
            for (col in 0..2) {
                var dot = 0.0
                for (k in 0..2) dot += r[row * 3 + k] * r[col * 3 + k]
                val want = if (row == col) 1.0 else 0.0
                if (abs(dot - want) > tol) return false
            }
        }
        return true
    }

    /**
     * Pinhole projection with Brown-Conrady distortion, matching
     * `cv::projectPoints` for the standard (non-fisheye) model with 4,
     * 5 or 8 coefficients (k1, k2, p1, p2[, k3[, k4, k5, k6]]).
     * Points behind the camera (z <= 0) yield null entries.
     */
    fun projectPoints(
        pointsMm: List<List<Double>>,
        rvec: List<Double>,
        tvec: List<Double>,
        fx: Double,
        fy: Double,
        cx: Double,
        cy: Double,
        distortion: List<Double>,
    ): List<List<Double>?> {
        require(rvec.size == 3 && tvec.size == 3) { "rvec/tvec must hold 3 doubles" }
        require(distortion.size in setOf(0, 4, 5, 8)) { "need 0/4/5/8 distortion coefficients" }
        val r = rodriguesToMatrix(rvec)
        val k = distortion + List(8 - distortion.size.coerceAtMost(8)) { 0.0 }
        return pointsMm.map { point ->
            val x = r[0] * point[0] + r[1] * point[1] + r[2] * point[2] + tvec[0]
            val y = r[3] * point[0] + r[4] * point[1] + r[5] * point[2] + tvec[1]
            val z = r[6] * point[0] + r[7] * point[1] + r[8] * point[2] + tvec[2]
            if (z <= 0.0) return@map null
            val xn = x / z
            val yn = y / z
            val r2 = xn * xn + yn * yn
            val r4 = r2 * r2
            val r6 = r4 * r2
            val radial = 1.0 + k[0] * r2 + k[1] * r4 + k[4] * r6
            val radial2 = if (k.size >= 8) 1.0 + k[5] * r2 + k[6] * r4 + k[7] * r6 else 1.0
            val xd = xn * radial / radial2 + 2.0 * k[2] * xn * yn + k[3] * (r2 + 2.0 * xn * xn)
            val yd = yn * radial / radial2 + k[2] * (r2 + 2.0 * yn * yn) + 2.0 * k[3] * xn * yn
            listOf(fx * xd + cx, fy * yd + cy)
        }
    }

    /** RMS reprojection residual in pixels over correspondences. */
    fun reprojectionRms(
        correspondences: List<Correspondence>,
        rvec: List<Double>,
        tvec: List<Double>,
        intrinsics: PoseEstimator.Intrinsics,
    ): Double? {
        if (correspondences.isEmpty()) return null
        val projected = projectPoints(
            correspondences.map { it.objectMm }, rvec, tvec,
            intrinsics.fx, intrinsics.fy, intrinsics.cx, intrinsics.cy,
            intrinsics.distortion,
        )
        var sum = 0.0
        var count = 0
        for (index in correspondences.indices) {
            val prediction = projected[index] ?: return null
            val observed = correspondences[index].imagePx
            val dx = prediction[0] - observed[0]
            val dy = prediction[1] - observed[1]
            sum += dx * dx + dy * dy
            count += 2
        }
        return if (count == 0) null else sqrt(sum / count)
    }

    /**
     * Corner-spread gate inputs: bounding-box width/height as fractions
     * of the frame, and convex-hull area as a fraction of frame area.
     * Thresholds live in [GuidanceHypothesis1] (`CORNER_SPREAD_FRAC`,
     * `HULL_AREA_FRAC`).
     */
    fun cornerSpread(pointsPx: List<List<Double>>, widthPx: Int, heightPx: Int): Spread {
        if (pointsPx.isEmpty() || widthPx <= 0 || heightPx <= 0) {
            return Spread(0.0, 0.0, 0.0)
        }
        val us = pointsPx.map { it[0] }
        val vs = pointsPx.map { it[1] }
        val fracW = (us.max() - us.min()) / widthPx
        val fracH = (vs.max() - vs.min()) / heightPx
        return Spread(fracW, fracH, hullAreaFrac(pointsPx, widthPx, heightPx))
    }

    data class Spread(val fracW: Double, val fracH: Double, val hullAreaFrac: Double)

    private fun hullAreaFrac(pointsPx: List<List<Double>>, widthPx: Int, heightPx: Int): Double {
        if (pointsPx.size < 3) return 0.0
        // Monotone-chain convex hull, then shoelace area.
        val sorted = pointsPx.sortedWith(compareBy({ it[0] }, { it[1] }))
        fun cross(o: List<Double>, a: List<Double>, b: List<Double>): Double =
            (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
        val lower = mutableListOf<List<Double>>()
        for (point in sorted) {
            while (lower.size >= 2 && cross(lower[lower.size - 2], lower.last(), point) <= 0.0) {
                lower.removeAt(lower.size - 1)
            }
            lower += point
        }
        val upper = mutableListOf<List<Double>>()
        for (point in sorted.asReversed()) {
            while (upper.size >= 2 && cross(upper[upper.size - 2], upper.last(), point) <= 0.0) {
                upper.removeAt(upper.size - 1)
            }
            upper += point
        }
        val hull = lower.dropLast(1) + upper.dropLast(1)
        if (hull.size < 3) return 0.0
        var area = 0.0
        for (i in hull.indices) {
            val a = hull[i]
            val b = hull[(i + 1) % hull.size]
            area += a[0] * b[1] - b[0] * a[1]
        }
        return abs(area) / 2.0 / (widthPx * heightPx)
    }

    // NOTE (deliberately no rotation helpers): ArUco corner order is
    // marker-intrinsic — corner[0] tracks the same physical corner at
    // any camera roll (proven by the cv2 oracle, see pnp-oracle.json
    // "corner_invariance"). The solver therefore works in native
    // buffer coordinates with native intrinsics at every device
    // orientation; no buffer rotation, index shifting, or intrinsics
    // rotation is needed or wanted. (An earlier rotated-intrinsics
    // derivation was disproven by the oracle: rotating distorted
    // normalized coordinates is not expressible as a coefficient
    // change.) Display mapping belongs to the overlay layer, which
    // uses CameraX CoordinateTransform.
}
