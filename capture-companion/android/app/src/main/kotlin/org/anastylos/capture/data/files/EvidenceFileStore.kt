package org.anastylos.capture.data.files

import android.graphics.BitmapFactory
import java.io.File
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Evidence bytes on disk. Layout:
 *
 *   evidence/originals/<observationId>/<imageId>.<ext>  — immutable originals
 *   evidence/previews/<observationId>/<imageId>.jpg     — derived display copies
 *
 * Originals are written atomically (temp file + rename) and never modified
 * afterwards; previews may be regenerated at any time.
 */
class EvidenceFileStore(
    val root: File,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) {

    fun originalFile(observationId: String, imageId: String, extension: String): File =
        File(File(root, "originals/$observationId"), "$imageId.$extension")

    fun previewFile(observationId: String, imageId: String): File =
        File(File(root, "previews/$observationId"), "$imageId.jpg")

    suspend fun storeOriginal(
        bytes: ByteArray,
        observationId: String,
        imageId: String,
        extension: String,
    ): File = withContext(ioDispatcher) {
        val target = originalFile(observationId, imageId, extension)
        target.parentFile?.mkdirs()
        val tmp = File.createTempFile("evidence-", ".part", target.parentFile)
        try {
            tmp.writeBytes(bytes)
            if (!tmp.renameTo(target)) {
                throw java.io.IOException("atomic move failed for $imageId")
            }
        } finally {
            if (tmp.exists()) tmp.delete()
        }
        target
    }

    /**
     * Decodes a downsampled preview without ever touching the original.
     * Returns false (never throws) when the source cannot be decoded.
     */
    fun makePreview(source: File, destination: File, maxDimension: Int = 1024): Boolean {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(source.absolutePath, bounds)
            val width = bounds.outWidth
            val height = bounds.outHeight
            if (width <= 0 || height <= 0) return false
            val options = BitmapFactory.Options().apply {
                inSampleSize = sampleSize(width, height, maxDimension)
            }
            val bitmap = BitmapFactory.decodeFile(source.absolutePath, options) ?: return false
            try {
                destination.parentFile?.mkdirs()
                destination.outputStream().use { out ->
                    if (!bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 85, out)) {
                        return false
                    }
                }
                true
            } finally {
                bitmap.recycle()
            }
        } catch (_: Exception) {
            false
        }
    }

    fun deleteObservationFiles(observationId: String) {
        File(root, "originals/$observationId").deleteRecursively()
        File(root, "previews/$observationId").deleteRecursively()
    }

    fun deleteImageFiles(observationId: String, imageId: String) {
        File(root, "originals/$observationId").listFiles { file -> file.nameWithoutExtension == imageId }
            ?.forEach { it.delete() }
        previewFile(observationId, imageId).delete()
    }

    companion object {
        /** Power-of-two downsampling that fits the longest side into maxDimension. */
        fun sampleSize(sourceWidth: Int, sourceHeight: Int, maxDimension: Int): Int {
            require(sourceWidth > 0 && sourceHeight > 0 && maxDimension > 0)
            var sample = 1
            val longest = maxOf(sourceWidth, sourceHeight)
            while (longest / (sample * 2) >= maxDimension) sample *= 2
            return sample
        }

        fun extensionForMime(mime: String): String = when (mime) {
            "image/jpeg" -> "jpg"
            "image/png" -> "png"
            "image/webp" -> "webp"
            "image/heic", "image/heif" -> "heic"
            else -> throw IllegalArgumentException("unsupported evidence mime: $mime")
        }
    }
}
