import comfy.options
comfy.options.enable_args_parsing()

import os
import importlib.util
import folder_paths
import time

def execute_prestartup_script():
    def execute_script(script_path):
        module_name = os.path.splitext(script_path)[0]
        try:
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return True
        except Exception as e:
            print(f"Failed to execute startup-script: {script_path} / {e}")
        return False

    node_paths = folder_paths.get_folder_paths("custom_nodes")
    for custom_node_path in node_paths:
        possible_modules = os.listdir(custom_node_path)
        node_prestartup_times = []

        for possible_module in possible_modules:
            module_path = os.path.join(custom_node_path, possible_module)
            if os.path.isfile(module_path) or module_path.endswith(".disabled") or module_path == "__pycache__":
                continue

            script_path = os.path.join(module_path, "prestartup_script.py")
            if os.path.exists(script_path):
                time_before = time.perf_counter()
                success = execute_script(script_path)
                node_prestartup_times.append((time.perf_counter() - time_before, module_path, success))
    if len(node_prestartup_times) > 0:
        print("\nPrestartup times for custom nodes:")
        for n in sorted(node_prestartup_times):
            if n[2]:
                import_message = ""
            else:
                import_message = " (PRESTARTUP FAILED)"
            print("{:6.1f} seconds{}:".format(n[0], import_message), n[1])
        print()

execute_prestartup_script()


# Main code
import asyncio
import itertools
import shutil
import threading
import gc

from comfy.cli_args import args

if os.name == "nt":
    import logging
    logging.getLogger("xformers").addFilter(lambda record: 'A matching Triton is not available' not in record.getMessage())

if __name__ == "__main__":
    if args.cuda_device is not None:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.cuda_device)
        print("Set cuda device to:", args.cuda_device)

    if args.deterministic:
        if 'CUBLAS_WORKSPACE_CONFIG' not in os.environ:
            os.environ['CUBLAS_WORKSPACE_CONFIG'] = ":4096:8"

    import cuda_malloc

import comfy.utils
import yaml

import execution
import server
from server import BinaryEventTypes
from nodes import init_custom_nodes
import comfy.model_management

def cuda_malloc_warning():
    device = comfy.model_management.get_torch_device()
    device_name = comfy.model_management.get_torch_device_name(device)
    cuda_malloc_warning = False
    if "cudaMallocAsync" in device_name:
        for b in cuda_malloc.blacklist:
            if b in device_name:
                cuda_malloc_warning = True
        if cuda_malloc_warning:
            print("\nWARNING: this card most likely does not support cuda-malloc, if you get \"CUDA error\" please run ComfyUI with: --disable-cuda-malloc\n")

def prompt_worker(q, server):
    e = execution.PromptExecutor(server)
    last_gc_collect = 0
    need_gc = False
    gc_collect_interval = 10.0

    while True:
        timeout = 1000.0
        if need_gc:
            timeout = max(gc_collect_interval - (current_time - last_gc_collect), 0.0)

        queue_item = q.get(timeout=timeout)
        if queue_item is not None:
            item, item_id = queue_item
            execution_start_time = time.perf_counter()
            prompt_id = item[1]
            server.last_prompt_id = prompt_id

            e.execute(item[2], prompt_id, item[3], item[4])
            need_gc = True
            q.task_done(item_id,
                        e.outputs_ui,
                        status=execution.PromptQueue.ExecutionStatus(
                            status_str='success' if e.success else 'error',
                            completed=e.success,
                            messages=e.status_messages))
            if server.client_id is not None:
                server.send_sync("executing", { "node": None, "prompt_id": prompt_id }, server.client_id)

            current_time = time.perf_counter()
            execution_time = current_time - execution_start_time
            print("Prompt executed in {:.2f} seconds".format(execution_time))

        flags = q.get_flags()
        free_memory = flags.get("free_memory", False)

        if flags.get("unload_models", free_memory):
            comfy.model_management.unload_all_models()
            need_gc = True
            last_gc_collect = 0

        if free_memory:
            e.reset()
            need_gc = True
            last_gc_collect = 0

        if need_gc:
            current_time = time.perf_counter()
            if (current_time - last_gc_collect) > gc_collect_interval:
                gc.collect()
                comfy.model_management.soft_empty_cache()
                last_gc_collect = current_time
                need_gc = False

async def run(server, address='', port=8188, verbose=True, call_on_start=None):
    await asyncio.gather(server.start(address, port, verbose, call_on_start), server.publish_loop())


def hijack_progress(server):
    def hook(value, total, preview_image):
        comfy.model_management.throw_exception_if_processing_interrupted()
        progress = {"value": value, "max": total, "prompt_id": server.last_prompt_id, "node": server.last_node_id}

        server.send_sync("progress", progress, server.client_id)
        if preview_image is not None:
            server.send_sync(BinaryEventTypes.UNENCODED_PREVIEW_IMAGE, preview_image, server.client_id)
    comfy.utils.set_progress_bar_global_hook(hook)


def cleanup_temp():
    temp_dir = folder_paths.get_temp_directory()
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def load_extra_path_config(yaml_path):
    with open(yaml_path, 'r') as stream:
        config = yaml.safe_load(stream)
    for c in config:
        conf = config[c]
        if conf is None:
            continue
        base_path = None
        if "base_path" in conf:
            base_path = conf.pop("base_path")
        for x in conf:
            for y in conf[x].split("\n"):
                if len(y) == 0:
                    continue
                full_path = y
                if base_path is not None:
                    full_path = os.path.join(base_path, full_path)
                print("Adding extra search path", x, full_path)
                folder_paths.add_model_folder_path(x, full_path)


class TerminalHook:
    def __init__(self):
        self.hooks = {}

    def add_hook(self, k, v):
        self.hooks[k] = v

    def remove_hook(self, k):
        if k in self.hooks:
            del self.hooks[k]

    def write_stderr(self, msg):
        for v in self.hooks.values():
            try:
                v.write_stderr(msg)
            except Exception:
                pass

    def write_stdout(self, msg):
        for v in self.hooks.values():
            try:
                v.write_stdout(msg)
            except Exception:
                pass

message_collapses = [] 
import_failed_extensions = set()
is_start_mode = True
is_import_fail_mode = False
std_log_lock = threading.Lock()
terminal_hook = TerminalHook()
def file_logging(enable_file_logging=True):
    import sys
    import re
    import datetime
    import atexit
    import platform
    try:
        if '--port' in sys.argv:
            port_index = sys.argv.index('--port')
            if port_index + 1 < len(sys.argv):
                port = int(sys.argv[port_index + 1])
                postfix = f"_{port}"
        else:
            postfix = ""

        # Logger setup
        if enable_file_logging:
            if os.path.exists(f"comfyui{postfix}.log"):
                if os.path.exists(f"comfyui{postfix}.prev.log"):
                    if os.path.exists(f"comfyui{postfix}.prev2.log"):
                        os.remove(f"comfyui{postfix}.prev2.log")
                    os.rename(f"comfyui{postfix}.prev.log", f"comfyui{postfix}.prev2.log")
                os.rename(f"comfyui{postfix}.log", f"comfyui{postfix}.prev.log")

            log_file = open(f"comfyui{postfix}.log", "w", encoding="utf-8", errors="ignore")

        log_lock = threading.Lock()

        original_stdout = sys.stdout
        original_stderr = sys.stderr

        if original_stdout.encoding.lower() == 'utf-8':
            write_stdout = original_stdout.write
            write_stderr = original_stderr.write
        else:
            def wrapper_stdout(msg):
                original_stdout.write(msg.encode('utf-8').decode(original_stdout.encoding, errors="ignore"))
                
            def wrapper_stderr(msg):
                original_stderr.write(msg.encode('utf-8').decode(original_stderr.encoding, errors="ignore"))

            write_stdout = wrapper_stdout
            write_stderr = wrapper_stderr

        pat_tqdm = r'\d+%.*\[(.*?)\]'
        pat_import_fail = r'seconds \(IMPORT FAILED\):'
        pat_custom_node = r'[/\\]custom_nodes[/\\](.*)$'

        class ComfyLogger:
            def __init__(self, is_stdout):
                self.is_stdout = is_stdout
                self.encoding = "utf-8"
                self.last_char = ''

            def fileno(self):
                try:
                    if self.is_stdout:
                        return original_stdout.fileno()
                    else:
                        return original_stderr.fileno()
                except AttributeError:
                    # Handle error
                    raise ValueError("The object does not have a fileno method")

            def write(self, message):
                global is_start_mode
                global is_import_fail_mode

                if any(f(message) for f in message_collapses):
                    return

                if is_start_mode:
                    if is_import_fail_mode:
                        match = re.search(pat_custom_node, message)
                        if match:
                            import_failed_extensions.add(match.group(1))
                            is_import_fail_mode = False
                    else:
                        match = re.search(pat_import_fail, message)
                        if match:
                            is_import_fail_mode = True
                        else:
                            is_import_fail_mode = False

                        if 'Starting server' in message:
                            is_start_mode = False

                if not self.is_stdout:
                    match = re.search(pat_tqdm, message)
                    if match:
                        message = re.sub(r'([#|])\d', r'\1▌', message)
                        message = re.sub('#', '█', message)
                        if '100%' in message:
                            self.sync_write(message)
                        else:
                            write_stderr(message)
                            original_stderr.flush()
                    else:
                        self.sync_write(message)
                else:
                    self.sync_write(message)

            def sync_write(self, message):
                with log_lock:
                    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    if self.last_char != '\n':
                        log_file.write(message)
                    else:
                        log_file.write(f"[{timestamp}] {message}")
                    log_file.flush()
                    self.last_char = message if message == '' else message[-1]

                with std_log_lock:
                    if self.is_stdout:
                        write_stdout(message)
                        original_stdout.flush()
                        terminal_hook.write_stderr(message)
                    else:
                        write_stderr(message)
                        original_stderr.flush()
                        terminal_hook.write_stdout(message)

            def flush(self):
                log_file.flush()

                with std_log_lock:
                    if self.is_stdout:
                        original_stdout.flush()
                    else:
                        original_stderr.flush()

            def close(self):
                self.flush()

            def reconfigure(self, *args, **kwargs):
                pass

            # You can close through sys.stderr.close_log()
            def close_log(self):
                sys.stderr = original_stderr
                sys.stdout = original_stdout
                log_file.close()
                
        def close_log():
            sys.stderr = original_stderr
            sys.stdout = original_stdout
            log_file.close()


        if enable_file_logging:
            sys.stdout = ComfyLogger(True)
            sys.stderr = ComfyLogger(False)

            atexit.register(close_log)
        else:
            sys.stdout.close_log = lambda: None

    except Exception as e:
        print(f"[ComfyUI-Manager] Logging failed: {e}")


    print("** ComfyUI startup time:", datetime.datetime.now())
    print("** Platform:", platform.system())
    print("** Python version:", sys.version)
    print("** Python executable:", sys.executable)

    if enable_file_logging:
        print("** Log path:", os.path.abspath('comfyui.log'))
    else:
        print("** Log path: file logging is disabled")

if __name__ == "__main__":
    if args.temp_directory:
        temp_dir = os.path.join(os.path.abspath(args.temp_directory), "temp")
        print(f"Setting temp directory to: {temp_dir}")
        folder_paths.set_temp_directory(temp_dir)
    cleanup_temp()
    file_logging(True)

    if args.windows_standalone_build:
        try:
            import new_updater
            new_updater.update_windows_updater()
        except:
            pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = server.PromptServer(loop)
    q = execution.PromptQueue(server)

    extra_model_paths_config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "extra_model_paths.yaml")
    if os.path.isfile(extra_model_paths_config_path):
        load_extra_path_config(extra_model_paths_config_path)

    if args.extra_model_paths_config:
        for config_path in itertools.chain(*args.extra_model_paths_config):
            load_extra_path_config(config_path)

    init_custom_nodes()

    cuda_malloc_warning()

    server.add_routes()
    hijack_progress(server)

    threading.Thread(target=prompt_worker, daemon=True, args=(q, server,)).start()

    if args.output_directory:
        output_dir = os.path.abspath(args.output_directory)
        print(f"Setting output directory to: {output_dir}")
        folder_paths.set_output_directory(output_dir)

    #These are the default folders that checkpoints, clip and vae models will be saved to when using CheckpointSave, etc.. nodes
    folder_paths.add_model_folder_path("checkpoints", os.path.join(folder_paths.get_output_directory(), "checkpoints"))
    folder_paths.add_model_folder_path("clip", os.path.join(folder_paths.get_output_directory(), "clip"))
    folder_paths.add_model_folder_path("vae", os.path.join(folder_paths.get_output_directory(), "vae"))

    if args.input_directory:
        input_dir = os.path.abspath(args.input_directory)
        print(f"Setting input directory to: {input_dir}")
        folder_paths.set_input_directory(input_dir)

    if args.quick_test_for_ci:
        exit(0)

    call_on_start = None
    if args.auto_launch:
        def startup_server(address, port):
            import webbrowser
            if os.name == 'nt' and address == '0.0.0.0':
                address = '127.0.0.1'
            webbrowser.open(f"http://{address}:{port}")
        call_on_start = startup_server

    try:
        loop.run_until_complete(run(server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start))
    except KeyboardInterrupt:
        print("\nStopped server")

    cleanup_temp()
