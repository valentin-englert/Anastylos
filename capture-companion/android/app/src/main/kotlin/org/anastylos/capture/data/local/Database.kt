package org.anastylos.capture.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters
import java.time.Instant
import org.anastylos.capture.domain.parseIsoUtc
import org.anastylos.capture.domain.toIsoUtc

class InstantConverter {
    @TypeConverter
    fun fromString(value: String): Instant =
        parseIsoUtc(value) ?: throw IllegalArgumentException("not ISO 8601 UTC: $value")

    @TypeConverter
    fun toString(value: Instant): String = value.toIsoUtc()
}

@Database(
    entities = [SessionEntity::class, ObservationEntity::class, ImageEntity::class],
    version = 1,
    exportSchema = true,
)
@TypeConverters(InstantConverter::class)
abstract class CaptureDatabase : RoomDatabase() {
    abstract fun sessions(): SessionDao

    abstract fun observations(): ObservationDao

    abstract fun images(): ImageDao

    companion object {
        fun open(context: Context): CaptureDatabase =
            Room.databaseBuilder(context, CaptureDatabase::class.java, "capture.db")
                .build()

        /** Tests only: an empty database with no durable side effects. */
        fun openInMemory(context: Context): CaptureDatabase =
            Room.inMemoryDatabaseBuilder(context, CaptureDatabase::class.java)
                .allowMainThreadQueries()
                .build()
    }
}
