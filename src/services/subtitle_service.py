"""
字幕生成和处理服务
"""
from pathlib import Path
from typing import List, Dict


class SubtitleService:
    """字幕服务"""
    
    def generate_from_audio(self, audio_file: str, model: str = "base") -> List[Dict]:
        """从音频自动生成字幕（使用 Whisper）"""
        try:
            import whisper
            whisper_model = whisper.load_model(model)
            result = whisper_model.transcribe(audio_file)
            
            subtitles = []
            for segment in result["segments"]:
                subtitles.append({
                    "text": segment["text"].strip(),
                    "start": segment["start"],
                    "end": segment["end"],
                    "x": 50,
                    "y": 80
                })
            return subtitles
        except ImportError:
            raise ImportError("请安装 openai-whisper: pip install openai-whisper")
    
    def load_srt(self, srt_file: str) -> List[Dict]:
        """加载 SRT 字幕文件"""
        subtitles = []
        with open(srt_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        blocks = content.strip().split('\n\n')
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                time_line = lines[1]
                start_str, end_str = time_line.split(' --> ')
                start = self._parse_srt_time(start_str)
                end = self._parse_srt_time(end_str)
                text = ' '.join(lines[2:])
                
                subtitles.append({
                    "text": text,
                    "start": start,
                    "end": end,
                    "x": 50,
                    "y": 80
                })
        return subtitles
    
    def save_srt(self, subtitles: List[Dict], output_file: str):
        """保存为 SRT 格式"""
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for i, sub in enumerate(subtitles, 1):
                start = self._format_srt_time(sub["start"])
                end = self._format_srt_time(sub["end"])
                f.write(f"{i}\n{start} --> {end}\n{sub['text']}\n\n")
    
    def _parse_srt_time(self, time_str: str) -> float:
        """解析 SRT 时间格式"""
        hours, minutes, rest = time_str.split(':')
        seconds, milliseconds = rest.split(',')
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
    
    def _format_srt_time(self, seconds: float) -> str:
        """格式化时间为 SRT 格式"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def snap_to_frame(self, time: float, fps: int = 30) -> float:
        """对齐到最近的帧"""
        frame_duration = 1.0 / fps
        return round(time / frame_duration) * frame_duration