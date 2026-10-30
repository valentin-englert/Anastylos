package org.anastylos.capture.guidance

/** Log tag for throttled GUIDE-01 analyzer diagnostics. */
internal const val DIAG_LOG_TAG = "AnastylosDiag"

/**
 * GUIDE-01 analyzer diagnostics: per-frame cost breakdown without
 * touching detection, pose, quality, or gate semantics. The analyzer
 * thread records copy/estimate/emit costs plus the frame quality
 * fractions; the UI thread records estimate age at publication. Every
 * [every]th frame yields one summary line; all other calls return
 * null so logcat stays quiet. Pure JVM, unit-tested; callers own the
 * actual logging.
 */
class TimingAccumulator(private val every: Int = 60) {
    private var detectSumMs = 0.0
    private var detectCalls = 0
    private var solveSumMs = 0.0
    private var solveCalls = 0

    private var frames = 0
    private var sumTotalMs = 0.0
    private var maxTotalMs = 0.0
    private var sumCopyMs = 0.0
    private var sumGapMs = 0.0
    private var maxGapMs = 0.0
    private var sumTsGapMs = 0.0
    private var maxTsGapMs = 0.0
    private var lastW = 0
    private var lastH = 0
    private var lastDark = Double.NaN
    private var lastSat = Double.NaN
    private var lastSharp = Double.NaN
    private var lastRoi = "n/a"
    private var lastFullDark = Double.NaN
    private var lastFullSat = Double.NaN
    private var sumSensorToArrivalMs = 0.0
    private var maxSensorToArrivalMs = 0.0
    private var minSensorToArrivalMs = Double.POSITIVE_INFINITY
    private var sumSensorToEmitMs = 0.0
    private var lastExposureMs = Double.NaN
    private var lastFrameDurationMs = Double.NaN

    private var uiFrames = 0
    private var sumUiAgeMs = 0.0
    private var maxUiAgeMs = 0.0
    private var sumUiGapMs = 0.0
    private var maxUiGapMs = 0.0
    private var uiFresh = 0

    /** Native detection cost of one call (analyzer thread). */
    @Synchronized
    fun recordDetect(detectMs: Double) {
        detectSumMs += detectMs
        detectCalls++
    }

    /** Native PnP cost of one call (analyzer thread). */
    @Synchronized
    fun recordSolve(solveMs: Double) {
        solveSumMs += solveMs
        solveCalls++
    }

    /**
     * One analyzed frame. Returns a summary line every [every]th
     * frame, else null. [gapMs] is the inter-frame arrival gap.
     */
    @Synchronized
    fun recordFrame(
        copyMs: Double,
        totalMs: Double,
        gapMs: Double,
        tsGapMs: Double,
        width: Int,
        height: Int,
        darkFraction: Double?,
        saturatedFraction: Double?,
        sharpness: Double?,
        sensorToArrivalMs: Double = 0.0,
        exposureRoi: String? = null,
        fullFrameDarkFraction: Double? = null,
        fullFrameSaturatedFraction: Double? = null,
        sensorToEmitMs: Double = 0.0,
        exposureMs: Double? = null,
        frameDurationMs: Double? = null,
    ): String? {
        if (exposureMs != null) lastExposureMs = exposureMs
        if (frameDurationMs != null) lastFrameDurationMs = frameDurationMs
        frames++
        if (sensorToArrivalMs < minSensorToArrivalMs) minSensorToArrivalMs = sensorToArrivalMs
        sumSensorToEmitMs += sensorToEmitMs
        sumSensorToArrivalMs += sensorToArrivalMs
        if (sensorToArrivalMs > maxSensorToArrivalMs) maxSensorToArrivalMs = sensorToArrivalMs
        if (exposureRoi != null) lastRoi = exposureRoi
        if (fullFrameDarkFraction != null) lastFullDark = fullFrameDarkFraction
        if (fullFrameSaturatedFraction != null) lastFullSat = fullFrameSaturatedFraction
        sumTotalMs += totalMs
        if (totalMs > maxTotalMs) maxTotalMs = totalMs
        sumCopyMs += copyMs
        sumGapMs += gapMs
        if (gapMs > maxGapMs) maxGapMs = gapMs
        sumTsGapMs += tsGapMs
        if (tsGapMs > maxTsGapMs) maxTsGapMs = tsGapMs
        lastW = width
        lastH = height
        if (darkFraction != null) lastDark = darkFraction
        if (saturatedFraction != null) lastSat = saturatedFraction
        if (sharpness != null) lastSharp = sharpness
        if (frames % every != 0) return null
        val n = frames.toDouble()
        val line = "frames=$frames ${lastW}x$lastH " +
            "totalAvg=${fmt(sumTotalMs / n)} totalMax=${fmt(maxTotalMs)} " +
            "copyAvg=${fmt(sumCopyMs / n)} " +
            "detectAvg=${fmt(detectSumMs / detectCalls.coerceAtLeast(1))} " +
            "solveAvg=${fmt(solveSumMs / solveCalls.coerceAtLeast(1))} " +
            "gapAvg=${fmt(sumGapMs / n)} gapMax=${fmt(maxGapMs)} " +
            "tsGapAvg=${fmt(sumTsGapMs / n)} tsGapMax=${fmt(maxTsGapMs)} " +
            "s2aAvg=${fmt(sumSensorToArrivalMs / n)} s2aMin=${fmt(minSensorToArrivalMs)} " +
            "s2aMax=${fmt(maxSensorToArrivalMs)} s2eAvg=${fmt(sumSensorToEmitMs / n)} " +
            "exp=${fmt(lastExposureMs)} frameDur=${fmt(lastFrameDurationMs)} " +
            "dark=${fmt3(lastDark)} sat=${fmt3(lastSat)} roi=$lastRoi " +
            "fullDark=${fmt3(lastFullDark)} fullSat=${fmt3(lastFullSat)} sharp=${fmt(lastSharp)}"
        resetFrameWindow()
        return line
    }

    /**
     * One estimate publication on the UI thread. Returns a summary
     * line every [every]th estimate, else null.
     */
    @Synchronized
    fun recordUi(ageMs: Long, gapMs: Double): String? {
        uiFrames++
        sumUiAgeMs += ageMs
        if (ageMs in 0..GuidanceHypothesis1.POSE_AGE_MS) uiFresh++
        if (ageMs > maxUiAgeMs) maxUiAgeMs = ageMs.toDouble()
        sumUiGapMs += gapMs
        if (gapMs > maxUiGapMs) maxUiGapMs = gapMs
        if (uiFrames % every != 0) return null
        val n = uiFrames.toDouble()
        val line = "ui=$uiFrames ageAvg=${fmt(sumUiAgeMs / n)} " +
            "ageMax=${fmt(maxUiAgeMs)} gapAvg=${fmt(sumUiGapMs / n)} " +
            "gapMax=${fmt(maxUiGapMs)} fresh=$uiFresh/$uiFrames"
        uiFrames = 0
        uiFresh = 0
        sumUiAgeMs = 0.0
        maxUiAgeMs = 0.0
        sumUiGapMs = 0.0
        maxUiGapMs = 0.0
        return line
    }

    private fun resetFrameWindow() {
        frames = 0
        sumTotalMs = 0.0
        maxTotalMs = 0.0
        sumCopyMs = 0.0
        sumGapMs = 0.0
        maxGapMs = 0.0
        sumTsGapMs = 0.0
        maxTsGapMs = 0.0
        sumSensorToArrivalMs = 0.0
        maxSensorToArrivalMs = 0.0
        minSensorToArrivalMs = Double.POSITIVE_INFINITY
        sumSensorToEmitMs = 0.0
        detectSumMs = 0.0
        detectCalls = 0
        solveSumMs = 0.0
        solveCalls = 0
    }

    private fun fmt(value: Double): String =
        if (value.isNaN()) "n/a" else "%.1f".format(java.util.Locale.US, value)

    private fun fmt3(value: Double): String =
        if (value.isNaN()) "n/a" else "%.3f".format(java.util.Locale.US, value)
}

/**
 * Native-backend decorator that times ArUco detection and PnP per
 * call and reports into [accumulator]. Delegates everything,
 * including release; timing never alters results.
 */
class TimingArucoBackend(
    private val delegate: OpenCvPoseEstimator.ArucoBackend,
    private val accumulator: TimingAccumulator,
) : OpenCvPoseEstimator.ArucoBackend {
    override fun detect(
        gray: ByteArray,
        width: Int,
        height: Int,
        dictionary: Int,
    ): List<PoseMath.MarkerDetection> {
        val start = System.nanoTime()
        try {
            return delegate.detect(gray, width, height, dictionary)
        } finally {
            val detectMs = (System.nanoTime() - start) / 1_000_000.0
            accumulator.recordDetect(detectMs)
        }
    }

    override fun solve(
        objectMm: List<List<Double>>,
        imagePx: List<List<Double>>,
        intrinsics: PoseEstimator.Intrinsics,
    ): OpenCvPoseEstimator.ArucoBackend.PnpSolution? {
        val start = System.nanoTime()
        try {
            return delegate.solve(objectMm, imagePx, intrinsics)
        } finally {
            val solveMs = (System.nanoTime() - start) / 1_000_000.0
            accumulator.recordSolve(solveMs)
        }
    }

    override fun release() = delegate.release()
}
