"""GUI 工作线程：生产任务后台执行与 FFmpeg 安装。"""
import asyncio
import sys
import traceback
from PyQt6.QtCore import QThread, pyqtSignal


class ProductionWorker(QThread):
    """制作工作线程"""
    progress = pyqtSignal(str, int, int)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, producer, **kwargs):
        super().__init__()
        self.producer = producer
        self.kwargs = kwargs
        self._running = True
        self._current_task = None

    def run(self):
        async def _run():
            try:
                kwargs = dict(self.kwargs)
                step = kwargs.pop('step', None)

                def progress_callback(msg, current, total):
                    if self._running:
                        self.progress.emit(msg, current, total)

                if step == 'story':
                    progress_callback("正在生成故事...", 0, 1)
                    result = await self.producer.generate_story(**kwargs)
                    progress_callback("故事生成完成", 1, 1)
                    self.finished.emit({'story': result})
                elif step == 'extract':
                    progress_callback("正在提取角色和场景...", 0, 1)
                    result = await self.producer.extract_from_story(**kwargs)
                    progress_callback("提取完成", 1, 1)
                    self.finished.emit({'extract': result})
                elif step == 'characters':
                    selected_characters = kwargs.pop('selected_characters', None)
                    result = await self.producer.generate_characters(
                        progress_callback=progress_callback,
                        selected_characters=selected_characters,
                    )
                    self.finished.emit({
                        'characters': [c.name for c in result],
                        'character_count': len(result),
                    })
                elif step == 'props':
                    result = await self.producer.generate_props(
                        progress_callback=progress_callback
                    )
                    self.finished.emit({
                        'props': [p.name for p in result],
                        'prop_count': len(result),
                    })
                elif step == 'costumes':
                    result = await self.producer.generate_costumes_only(
                        progress_callback=progress_callback,
                        selected_characters=kwargs.pop('selected_characters', None),
                        selected_costumes=kwargs.pop('selected_costumes', None),
                        force=kwargs.pop('force', False),
                    )
                    self.finished.emit({
                        'costume_results': result,
                        'costume_count': len(result),
                    })
                elif step == 'scenes':
                    result = await self.producer.generate_scenes(
                        progress_callback=progress_callback,
                        selected_scenes=kwargs.pop('selected_scenes', None),
                    )
                    self.finished.emit({'scenes': [s.name for s in result]})
                elif step == 'storyboard':
                    selected_indices = kwargs.pop('selected_indices', None)
                    result = await self.producer.generate_storyboard(
                        progress_callback=progress_callback,
                        selected_indices=selected_indices,
                    )
                    self.finished.emit({'storyboard': len(result)})
                elif step == 'single_storyboard_image':
                    scene_index = kwargs.pop('scene_index')
                    frame_path = await self.producer.generate_single_storyboard_image(
                        scene_index,
                        progress_callback=progress_callback,
                    )
                    self.finished.emit({
                        'single_storyboard_image': scene_index,
                        'frame_path': frame_path,
                    })
                elif step == 'single_storyboard_video':
                    scene_index = kwargs.pop('scene_index')
                    video_path = await self.producer.generate_scene_video(
                        scene_index,
                        progress_callback=progress_callback,
                    )
                    if not video_path:
                        raise RuntimeError(f"分镜 {scene_index + 1} 视频生成失败")
                    self.finished.emit({
                        'single_storyboard_video': scene_index,
                        'video_path': video_path,
                    })
                elif step == 'storyboard_script':
                    result = await self.producer.generate_storyboard_script(
                        progress_callback=progress_callback
                    )
                    self.finished.emit({'storyboard': len(result)})
                elif step == 'episode_storyboard':
                    result = await self.producer.generate_episode_storyboard(
                        kwargs.pop('episode_index'),
                        progress_callback=progress_callback,
                    )
                    self.finished.emit({'episode_storyboard': result})
                elif step == 'video':
                    selected_indices = kwargs.pop('selected_indices', None)
                    result = await self.producer.generate_video(
                        progress_callback=progress_callback,
                        selected_indices=selected_indices,
                    )
                    self.finished.emit({'video': result})
                elif step == 'retry_video':
                    failed_indices = kwargs.pop('failed_indices', [])
                    success_count = 0
                    failed_list = []
                    for idx in failed_indices:
                        if not self._running:
                            break
                        res = await self.producer.generate_scene_video(
                            idx,
                            progress_callback=progress_callback
                        )
                        if res:
                            success_count += 1
                        else:
                            failed_list.append(idx)
                    self.finished.emit({
                        'video': {
                            'success_count': success_count,
                            'total_count': len(failed_indices),
                            'failed_indices': failed_list
                        }
                    })
                elif step == 'merge_video':
                    result = await self.producer.merge_videos()
                    self.finished.emit({'video': result})
                elif step == 'analyze_story':
                    story = kwargs.pop('story', '')
                    target_minutes = kwargs.pop('target_minutes', 3)
                    result = await self.producer.analyze_story(
                        story=story,
                        target_minutes=target_minutes,
                    )
                    self.finished.emit({'analyze_story': result})
                elif step == 'split_story':
                    story = kwargs.pop('story', '')
                    split_plan = kwargs.pop('split_plan', [])
                    story_file_path = kwargs.pop('story_file_path', None)
                    result = await self.producer.split_story_to_files(
                        story=story,
                        split_plan=split_plan,
                        story_file_path=story_file_path,
                    )
                    self.finished.emit({'split_story': result})
            except asyncio.CancelledError:
                # 任务被取消，正常退出路径
                print("⏹ 任务已取消", file=sys.stderr)
            except Exception as e:
                error_trace = traceback.format_exc()
                print("\n" + "="*60, file=sys.stderr)
                print(f"❌ ProductionWorker 异常 (step={step})", file=sys.stderr)
                print("="*60, file=sys.stderr)
                print(f"错误类型: {type(e).__name__}", file=sys.stderr)
                print(f"错误: {str(e)}", file=sys.stderr)
                print("-"*60, file=sys.stderr)
                print("堆栈跟踪:", file=sys.stderr)
                print(error_trace, file=sys.stderr)
                print("="*60 + "\n", file=sys.stderr)
                if self._running:
                    self.error.emit(f"{type(e).__name__}: {str(e)}")
            except BaseException as e:
                # 网络层泄漏的 CancelledError（aiohttp DNS shield）不属于 Exception，
                # 旧版会穿透本 except、穿透 run()，QThread 静默退出，界面永远停在
                # 「生成中」且看不到任何报错。这里兜底上报，保证线程正常收尾
                # （已生成的成果由 producer._auto_save 单独保存，不会丢）。
                error_trace = traceback.format_exc()
                print("\n" + "="*60, file=sys.stderr)
                print(f"❌ ProductionWorker 底层异常 (step={step})", file=sys.stderr)
                print("="*60, file=sys.stderr)
                print(f"错误类型: {type(e).__name__}", file=sys.stderr)
                print(f"错误: {str(e)}", file=sys.stderr)
                print("堆栈跟踪:", file=sys.stderr)
                print(error_trace, file=sys.stderr)
                print("="*60 + "\n", file=sys.stderr)
                if self._running:
                    self.error.emit(f"{type(e).__name__}: {str(e)}")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # 创建 task 以便可以取消
                self._current_task = loop.create_task(_run())
                loop.run_until_complete(self._current_task)
            finally:
                loop.close()
        except BaseException as e:
            error_trace = traceback.format_exc()
            print(f"❌ 事件循环异常: {e}\n{error_trace}", file=sys.stderr)
            self.error.emit(f"事件循环异常: {e}")

    def cancel(self):
        """安全取消：置位 _running 并取消当前异步任务。"""
        self._running = False
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    def terminate(self):
        # 保留 API 兼容：等价于 cancel + 等待线程自然结束
        self.cancel()
        self.wait(15000)


class FfmpegInstallWorker(QThread):
    """后台线程：自动下载安装 FFmpeg 到 tools/ffmpeg/bin。

    GUI 侧提供「⬇ 安装 FFmpeg」一键按钮；下载/解压期间可取消。
    结果通过 finished_install(是否成功, 说明) 返回（说明可直接展示给用户）。
    """

    progress = pyqtSignal(int, int, str)          # 已下载, 总大小, 阶段说明
    finished_install = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from src.services import media_tools

        def cb(done, total, msg):
            self.progress.emit(int(done or 0), int(total or 0), str(msg))

        try:
            ok, msg = media_tools.download_ffmpeg(cb, should_cancel=lambda: self._cancel)
        except TypeError:
            # 兼容旧签名（无 should_cancel）
            ok, msg = media_tools.download_ffmpeg(cb)
        except Exception as e:
            ok, msg = False, f"安装失败: {e}"
        self.finished_install.emit(ok, msg)