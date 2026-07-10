from pydub import AudioSegment
from pydub.effects import speedup


class OverlapFixer:
    """
    Исправляет перекрытие сегментов TTS.
    """

    SPEEDS = [1.05, 1.10, 1.15, 1.20]

    @staticmethod
    def fix(segments):
        fixed = []
        previous_end = 0

        for segment in segments:
            start = segment["start"]
            end = segment["end"]
            path = segment["audio_path"]

            audio = AudioSegment.from_file(path)
            duration = len(audio)

            original_duration = end - start

            if duration > original_duration:
                for rate in OverlapFixer.SPEEDS:
                    accelerated = speedup(audio, playback_speed=rate)

                    if len(accelerated) <= original_duration:
                        audio = accelerated
                        duration = len(audio)
                        accelerated.export(path, format="mp3")
                        break

            overlap = previous_end - start

            if overlap > 0:
                start = previous_end
                end = start + duration

            segment["start"] = start
            segment["end"] = end
            segment["duration"] = duration

            fixed.append(segment)

            previous_end = end

        return fixed
