package org.anastylos.capture.sync

import java.time.Instant
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.anastylos.capture.data.local.toDto
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.MAX_ENTRIES
import org.anastylos.capture.domain.MAX_MANIFEST_BYTES
import org.anastylos.capture.domain.SCHEMA_VERSION
import org.anastylos.capture.domain.isId
import org.anastylos.capture.domain.newId
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.domain.sha256Hex
import org.anastylos.capture.domain.toIsoUtc
import org.anastylos.capture.domain.validateObservationForExport

/**
 * Native package builder: the PWA's `planExport` + `buildPackage`,
 * mirrored step for step so the Python validator cannot tell them apart.
 *
 * Pure JVM: callers supply domain objects plus byte resolvers, so the
 * whole path — plan, re-hash, manifest, ZIP — is unit-testable without
 * a device. Slice A builds single-session frame packages (one session,
 * one observation per sampled frame); the builder itself accepts any
 * same-session observation list.
 */
data class PackagePlanIssue(
    val observationId: String?,
    val imageId: String?,
    val message: String,
)

data class BuiltFramePackage(
    val packageId: String,
    /** Exact `manifest.json` bytes, PWA-spelled. */
    val manifestJson: String,
    val bytes: ByteArray,
    val fileCount: Int,
    val observationIds: List<String>,
    val packageSha256: String,
) {
    override fun equals(other: Any?): Boolean =
        other is BuiltFramePackage &&
            packageId == other.packageId &&
            manifestJson == other.manifestJson &&
            bytes.contentEquals(other.bytes)

    override fun hashCode(): Int = 31 * packageId.hashCode() + bytes.contentHashCode()
}

class PackageBuildException(message: String) : IllegalArgumentException(message)

data class PreviewBytes(
    val mime: String,
    val bytes: ByteArray,
)

fun assetPath(observationId: String, filename: String): String =
    "assets/$observationId/$filename"

fun previewPath(observationId: String, imageId: String): String =
    "previews/$observationId/$imageId.jpg"

/**
 * Gather and validate everything a package needs. A non-empty issue list
 * blocks the build, exactly like the PWA planner.
 */
fun planFramePackage(
    session: CaptureSession,
    observations: List<ComponentObservation>,
    resolveOriginal: (imageId: String) -> ByteArray?,
    packageId: String = newId(),
): List<PackagePlanIssue> {
    val issues = mutableListOf<PackagePlanIssue>()
    if (!isId(packageId)) {
        return listOf(PackagePlanIssue(null, null, "package_id is not a valid id"))
    }
    val unique = observations.distinctBy { it.observationId }
    if (unique.isEmpty()) {
        return listOf(PackagePlanIssue(null, null, "Nothing selected for export."))
    }
    for (obs in unique) {
        if (obs.sessionId != session.sessionId) {
            issues += PackagePlanIssue(
                obs.observationId,
                null,
                "Observation belongs to another session; frame packages stay single-session.",
            )
            continue
        }
        for (issue in validateObservationForExport(obs)) {
            issues += PackagePlanIssue(obs.observationId, null, issue.message)
        }
        if (obs.images.isEmpty()) {
            issues += PackagePlanIssue(
                obs.observationId,
                null,
                "Observation has no photo evidence; capture at least one photo first.",
            )
        }
        for (image in obs.images) {
            val original = resolveOriginal(image.imageId)
            if (original == null) {
                issues += PackagePlanIssue(
                    obs.observationId,
                    image.imageId,
                    "Original bytes for ${image.filename} are missing; " +
                        "refusing to export without evidence.",
                )
                continue
            }
            if (original.size.toLong() != image.byteLength) {
                issues += PackagePlanIssue(
                    obs.observationId,
                    image.imageId,
                    "Stored bytes for ${image.filename} disagree with the recorded length.",
                )
                continue
            }
            if (original.sha256Hex() != image.sha256) {
                issues += PackagePlanIssue(
                    obs.observationId,
                    image.imageId,
                    "Stored bytes for ${image.filename} fail the hash check; " +
                        "evidence may be damaged.",
                )
            }
        }
    }
    return issues
}

/**
 * Build the ZIP bytes for a clean plan. Throws [PackageBuildException]
 * when planning reports issues — nothing partial ever leaves the builder.
 */
fun buildFramePackage(
    session: CaptureSession,
    observations: List<ComponentObservation>,
    resolveOriginal: (imageId: String) -> ByteArray?,
    appVersion: String,
    packageId: String = newId(),
    exportedAt: Instant = nowUtc(),
    mtimeMillis: Long? = null,
    resolvePreview: ((imageId: String) -> PreviewBytes?)? = null,
): BuiltFramePackage {
    val issues = planFramePackage(session, observations, resolveOriginal, packageId)
    if (issues.isNotEmpty()) {
        throw PackageBuildException("Export blocked: ${issues.first().message}")
    }
    val unique = observations.distinctBy { it.observationId }.sortedBy { it.capturedAt }

    val manifest = ManifestDto(
        schemaVersion = SCHEMA_VERSION,
        packageId = packageId,
        exportedAt = exportedAt.toIsoUtc(),
        generator = GeneratorDto(app = org.anastylos.capture.domain.APP_ID, appVersion = appVersion),
        sessions = listOf(session.toManifestDto()),
        observations = emptyList(),
        files = emptyList(),
    )

    data class Entry(val path: String, val data: ByteArray)

    val entries = mutableListOf<Entry>()
    val files = mutableListOf<ManifestFileDto>()
    val crops = linkedMapOf<String, CropEntryDto>()
    val manifestObservations = unique.map { obs ->
        for (image in obs.images) {
            val data = requireNotNull(resolveOriginal(image.imageId)) {
                "planner accepted ${image.imageId} but its bytes vanished"
            }
            val path = assetPath(obs.observationId, image.filename)
            entries += Entry(path, data)
            files += ManifestFileDto(
                path = path,
                kind = "original",
                observationId = obs.observationId,
                imageId = image.imageId,
                sha256 = image.sha256,
                bytes = image.byteLength,
                mime = image.mime,
            )
            val preview = resolvePreview?.invoke(image.imageId)
            if (preview != null) {
                val previewEntry = previewPath(obs.observationId, image.imageId)
                entries += Entry(previewEntry, preview.bytes)
                files += ManifestFileDto(
                    path = previewEntry,
                    kind = "preview",
                    observationId = obs.observationId,
                    imageId = image.imageId,
                    sha256 = preview.bytes.sha256Hex(),
                    bytes = preview.bytes.size.toLong(),
                    mime = preview.mime,
                )
            }
            image.crop?.let { crops[image.imageId] = CropEntryDto(it.toDto()) }
        }
        obs.toManifestDto()
    }

    // Deterministic order: manifest first, then assets, previews, annotations.
    val sortedFiles = files.sortedBy { it.path }
    val sortedEntries = entries.sortedBy { it.path }
    val finalManifest = manifest.copy(observations = manifestObservations, files = sortedFiles)
    val manifestJson = ManifestJson.encodeToString(ManifestDto.serializer(), finalManifest) + "\n"
    val manifestBytes = manifestJson.toByteArray(Charsets.UTF_8)
    if (manifestBytes.size.toLong() > MAX_MANIFEST_BYTES) {
        throw PackageBuildException("Manifest exceeds the 5 MiB cap; split the package.")
    }

    val allEntries = mutableListOf(Entry("manifest.json", manifestBytes))
    allEntries += sortedEntries
    if (crops.isNotEmpty()) {
        val annotations = ManifestJson.encodeToString(
            AnnotationsDto.serializer(),
            AnnotationsDto(crops),
        ) + "\n"
        allEntries += Entry("annotations.json", annotations.toByteArray(Charsets.UTF_8))
    }
    if (allEntries.size > MAX_ENTRIES) {
        throw PackageBuildException("Package exceeds the entry cap; split the package.")
    }
    val zipBytes = writeStoredZip(allEntries.map { it.path to it.data }, mtimeMillis)
    return BuiltFramePackage(
        packageId = packageId,
        manifestJson = manifestJson,
        bytes = zipBytes,
        fileCount = allEntries.size,
        observationIds = unique.map { it.observationId },
        packageSha256 = zipBytes.sha256Hex(),
    )
}

private fun writeStoredZip(entries: List<Pair<String, ByteArray>>, mtimeMillis: Long?): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    ZipOutputStream(out).use { zip ->
        for ((path, data) in entries) {
            val entry = ZipEntry(path)
            entry.method = ZipEntry.STORED
            entry.size = data.size.toLong()
            entry.compressedSize = data.size.toLong()
            entry.crc = CRC32().also { it.update(data) }.value
            if (mtimeMillis != null) entry.time = mtimeMillis
            zip.putNextEntry(entry)
            zip.write(data)
            zip.closeEntry()
        }
    }
    return out.toByteArray()
}
