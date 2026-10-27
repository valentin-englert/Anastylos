package org.anastylos.capture.data.local

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface SessionDao {
    @Query("SELECT * FROM sessions ORDER BY updated_at DESC")
    fun observeAll(): Flow<List<SessionEntity>>

    @Query("SELECT * FROM sessions WHERE session_id = :id")
    suspend fun getById(id: String): SessionEntity?

    @Query("SELECT * FROM sessions WHERE session_id = :id")
    fun observeById(id: String): Flow<SessionEntity?>

    @Upsert
    suspend fun upsert(session: SessionEntity)

    @Query("DELETE FROM sessions WHERE session_id = :id")
    suspend fun deleteById(id: String)

    @Query("SELECT COUNT(*) FROM sessions")
    suspend fun count(): Int
}

@Dao
interface ObservationDao {
    @Query("SELECT * FROM observations WHERE session_id = :sessionId ORDER BY captured_at ASC")
    fun observeBySession(sessionId: String): Flow<List<ObservationEntity>>

    @Query("SELECT * FROM observations WHERE observation_id = :id")
    suspend fun getById(id: String): ObservationEntity?

    @Query("SELECT * FROM observations WHERE observation_id = :id")
    fun observeById(id: String): Flow<ObservationEntity?>

    @Query("SELECT * FROM observations ORDER BY captured_at ASC")
    suspend fun getAll(): List<ObservationEntity>

    @Upsert
    suspend fun upsert(observation: ObservationEntity)

    @Query("DELETE FROM observations WHERE observation_id = :id")
    suspend fun deleteById(id: String)

    @Query("SELECT COUNT(*) FROM observations WHERE session_id = :sessionId")
    suspend fun countBySession(sessionId: String): Int
}

@Dao
interface ImageDao {
    @Query("SELECT * FROM images WHERE observation_id = :observationId ORDER BY captured_at ASC")
    fun observeByObservation(observationId: String): Flow<List<ImageEntity>>

    @Query("SELECT * FROM images WHERE observation_id = :observationId ORDER BY captured_at ASC")
    suspend fun getByObservation(observationId: String): List<ImageEntity>

    @Query("SELECT * FROM images WHERE image_id = :id")
    suspend fun getById(id: String): ImageEntity?

    @Upsert
    suspend fun upsert(image: ImageEntity)

    @Query("DELETE FROM images WHERE image_id = :id")
    suspend fun deleteById(id: String)

    @Query("SELECT COUNT(*) FROM images WHERE observation_id = :observationId")
    suspend fun countByObservation(observationId: String): Int
}
