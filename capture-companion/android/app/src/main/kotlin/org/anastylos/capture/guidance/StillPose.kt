package org.anastylos.capture.guidance

/**
 * The analysis pose associated with a still: the frame exposed last at or
 * before the still's exposure, both on the sensor clock. [sampleAgeMs] is the
 * exposure gap the sidecar reports and the 150 ms gate judges.
 */
data class StillPose(val estimate: PoseEstimator.Estimate, val sampleAgeMs: Double)

/** Null when no recent frame was exposed before the still. */
fun poseForStill(recent: List<PoseEstimator.Estimate>, stillSensorNs: Long): StillPose? =
    recent.filter { it.sensorTimestampNs <= stillSensorNs }
        .maxByOrNull { it.sensorTimestampNs }
        ?.let { StillPose(it, (stillSensorNs - it.sensorTimestampNs) / 1_000_000.0) }
