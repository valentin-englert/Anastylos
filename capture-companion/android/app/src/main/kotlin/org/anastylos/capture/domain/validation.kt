package org.anastylos.capture.domain

/**
 * Domain validation, mirroring the Python validator rule for rule.
 *
 * Two levels: drafts (what the user may save at any moment) and export
 * readiness (what a package may contain). Drafts stay lenient so capturing
 * never blocks on metadata; export stays strict so invalid evidence can
 * never leave the phone.
 */
data class ValidationIssue(
    val path: String,
    val message: String,
)

fun validateSessionDraft(session: CaptureSession): List<ValidationIssue> {
    val issues = mutableListOf<ValidationIssue>()
    if (!isId(session.sessionId)) issues += ValidationIssue("session_id", "must be a UUID")
    if (session.name.isBlank()) issues += ValidationIssue("name", "must not be blank")
    if (session.name.length > 200) issues += ValidationIssue("name", "must be at most 200 characters")
    return issues
}

private fun <T> checkConfirmedField(
    field: ConfirmedField<T>,
    what: String,
    isValidValue: (T) -> Boolean,
    issues: MutableList<ValidationIssue>,
) {
    when (field.state) {
        EpistemicState.CONFIRMED -> {
            val value = field.value
            if (value == null || !isValidValue(value)) {
                issues += ValidationIssue(what, "is confirmed but has no valid value")
            }
        }
        EpistemicState.UNKNOWN -> {
            if (field.value != null) {
                issues += ValidationIssue(what, "is unknown but carries a value; unknowns stay null")
            }
        }
        else -> issues += ValidationIssue(what, "state must be confirmed or unknown")
    }
}

fun validateObservationDraft(obs: ComponentObservation): List<ValidationIssue> {
    val issues = mutableListOf<ValidationIssue>()
    if (!isId(obs.observationId)) issues += ValidationIssue("observation_id", "must be a UUID")
    if (!isId(obs.sessionId)) issues += ValidationIssue("session_id", "must be a UUID")
    for ((index, marking) in obs.markings.withIndex()) {
        if (marking.text.isBlank()) {
            issues += ValidationIssue("markings[$index]", "needs non-empty text")
        } else if (marking.state != EpistemicState.OBSERVED) {
            issues += ValidationIssue("markings[$index]", "state must be 'observed'")
        }
    }
    for ((index, m) in obs.measurements.withIndex()) {
        if (!m.value.isFinite()) issues += ValidationIssue("measurements[$index]", "value must be a number")
        if (m.unit.isBlank()) issues += ValidationIssue("measurements[$index]", "needs an explicit unit")
    }
    checkConfirmedField(obs.category, "category", { it.isNotBlank() }, issues)
    checkConfirmedField(obs.quantity, "quantity", { it >= 1 }, issues)
    for ((index, candidate) in obs.candidates.withIndex()) {
        if (candidate.label.isBlank()) {
            issues += ValidationIssue("candidates[$index]", "label must be a non-empty string")
        }
        if (candidate.provider.isBlank() || candidate.model.isBlank()) {
            issues += ValidationIssue("candidates[$index]", "needs provider and model identifiers")
        }
    }
    for (image in obs.images) issues += validateImage(image).map {
        ValidationIssue("images[${image.imageId}].${it.path}", it.message)
    }
    return issues
}

fun isSafeFilename(name: String): Boolean {
    if (name.isBlank() || name.length > 1024) return false
    if ("/" in name || "\\" in name) return false
    if (name == "." || name == "..") return false
    return true
}

fun validateImage(image: EvidenceImage): List<ValidationIssue> {
    val issues = mutableListOf<ValidationIssue>()
    if (!isId(image.imageId)) issues += ValidationIssue("image_id", "must be a UUID")
    if (!isId(image.observationId)) issues += ValidationIssue("observation_id", "must be a UUID")
    if (!isSafeFilename(image.filename)) {
        issues += ValidationIssue("filename", "is empty or unsafe")
    }
    if (image.mime !in IMAGE_MIMES) {
        issues += ValidationIssue("mime", "${image.mime} is not an accepted image type")
    }
    if (image.byteLength <= 0) issues += ValidationIssue("byte_length", "must be positive")
    if (!isSha256Hex(image.sha256)) issues += ValidationIssue("sha256", "must be lowercase hex")
    image.crop?.let { crop ->
        val box = listOf(crop.x, crop.y, crop.width, crop.height)
        if (box.any { !it.isFinite() } || box.any { it < 0.0 || it > 1.0 }) {
            issues += ValidationIssue("crop", "must be normalized 0..1 coordinates")
        }
        if (crop.width <= 0.0 || crop.height <= 0.0) {
            issues += ValidationIssue("crop", "must have positive size")
        }
    }
    return issues
}

/**
 * Export readiness for one observation: everything the validator would
 * refuse, refused here first. In particular an observation without photos
 * can exist locally but can never ship.
 */
fun validateObservationForExport(obs: ComponentObservation): List<ValidationIssue> {
    val issues = validateObservationDraft(obs).toMutableList()
    if (obs.images.isEmpty()) {
        issues += ValidationIssue("images", "at least one photo is required to export")
    }
    for (image in obs.images) {
        if (image.byteLength > MAX_FILE_BYTES) {
            issues += ValidationIssue(
                "images[${image.imageId}].byte_length",
                "exceeds the per-file cap",
            )
        }
    }
    return issues
}
