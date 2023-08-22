#!/usr/bin/env python
''' Contains the handler function that will be called by the serverless worker. '''

# Start the VLLM serving layer on our RunPod worker.
from typing import Generator
from metrics import vllm_log_system_stats
from templates import DEFAULT_TEMPLATE, LLAMA_TEMPLATE
from vllm import AsyncLLMEngine, SamplingParams, AsyncEngineArgs
from vllm.utils import random_uuid
import runpod
import os

# Prepare the model and tokenizer
MODEL_NAME = os.environ.get('MODEL_NAME')
MODEL_BASE_PATH = os.environ.get('MODEL_BASE_PATH', '/runpod-volume/')
STREAMING = os.environ.get('STREAMING', False) == 'True'
TOKENIZER = os.environ.get('TOKENIZER', None)


if not MODEL_NAME:
    print("Error: The model has not been provided.")

# Tensor parallelism
try:
    NUM_GPU_SHARD = int(os.environ.get('NUM_GPU_SHARD', 1))
except ValueError:
    print("Error: NUM_GPU_SHARD should be an integer. Using default value of 1.")
    NUM_GPU_SHARD = 1

# Prepare the engine's arguments
engine_args = AsyncEngineArgs(
    model=f"{MODEL_BASE_PATH}{MODEL_NAME.split('/')[1]}",
    tokenizer=TOKENIZER,
    tokenizer_mode="auto",
    tensor_parallel_size=NUM_GPU_SHARD,
    dtype="auto",
    seed=0,
    worker_use_ray=False,
    max_num_batched_tokens=10000,
    max_num_seqs=4096
)

# Create the vLLM asynchronous engine
llm = AsyncLLMEngine.from_engine_args(engine_args)

# Incorporate metrics tracking
llm.engine._log_system_stats = vllm_log_system_stats

def concurrency_controller() -> bool:
    # Compute pending sequences
    total_pending_sequences = len(llm.engine.scheduler.waiting) + len(llm.engine.scheduler.swapped)
    print("vLLM has {total_pending_sequences} pending sequences in its internal queue.")

    # If we have over 30 pending sequences, then we'll start auto-scaling.
    return total_pending_sequences > 30


def prepare_metrics() -> dict:
    # The vLLM metrics are updated every 5 seconds, see metrics.py for the _LOGGING_INTERVAL_SEC field.
    return llm.engine.metrics


# Validation
def validate_sampling_params(sampling_params):
    def validate_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def validate_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def validate_bool(value, default):
        if isinstance(value, bool):
            return value
        return default

    n = validate_int(sampling_params.get('n'), 1)
    best_of = validate_int(sampling_params.get('best_of'), None)
    presence_penalty = validate_float(
        sampling_params.get('presence_penalty'), 0.0)
    frequency_penalty = validate_float(
        sampling_params.get('frequency_penalty'), 0.0)
    temperature = validate_float(sampling_params.get('temperature'), 1.0)
    top_p = validate_float(sampling_params.get('top_p'), 1.0)
    top_k = validate_int(sampling_params.get('top_k'), -1)
    use_beam_search = validate_bool(
        sampling_params.get('use_beam_search'), False)
    stop = sampling_params.get('stop', None)
    ignore_eos = validate_bool(sampling_params.get('ignore_eos'), False)
    max_tokens = validate_int(sampling_params.get('max_tokens'), 256)
    logprobs = validate_float(sampling_params.get('logprobs'), None)

    return {
        'n': n,
        'best_of': best_of,
        'presence_penalty': presence_penalty,
        'frequency_penalty': frequency_penalty,
        'temperature': temperature,
        'top_p': top_p,
        'top_k': top_k,
        'use_beam_search': use_beam_search,
        'stop': stop,
        'ignore_eos': ignore_eos,
        'max_tokens': max_tokens,
        'logprobs': logprobs,
    }


async def handler_streaming(job: dict) -> Generator[dict[str, list], None, None]:
    '''
    This is the handler function that will be called by the serverless worker.
    '''
    print("Job received by handler: {}".format(job))

    # Get job input
    job_input = job['input']

    # Prompts
    if MODEL_NAME.lower() == "llama-2-7b-chat-hf" or MODEL_NAME.lower() == "llama-2-13b-chat-hf":
        template = LLAMA_TEMPLATE
    else:
        template = DEFAULT_TEMPLATE

    # Use the template
    prompt = template(job_input['prompt'])

    # Validate the inputs
    sampling_params = job_input.get('sampling_params', None)
    if sampling_params:
        sampling_params = validate_sampling_params(sampling_params)

        # Sampling parameters
        # https://github.com/vllm-project/vllm/blob/main/vllm/sampling_params.py#L7
        sampling_params = SamplingParams(**sampling_params)
    else:
        sampling_params = SamplingParams()

    # Print the job input
    print(job_input)

    # Print the sampling params
    print(sampling_params)

    # Send request to VLLM
    request_id = random_uuid()
    results_generator = llm.generate(prompt, sampling_params, request_id)

    # Streaming case
    async for request_output in results_generator:
        prompt = request_output.prompt
        text_outputs = [
            output.text for output in request_output.outputs
        ]

        # Metrics for the vLLM serverless worker
        metrics = prepare_metrics()
        metrics['job_input'] = job_input
        metrics['input_tokens'] = [len(request_output.prompt_token_ids)] * len(request_output.outputs)
        metrics['output_tokens'] = [len(output.token_ids) for output in request_output.outputs]

        ret = {
            "text": text_outputs,
            "runpod_internal": {
                "metrics": metrics
            }
        }
        yield ret


async def handler(job: dict) -> dict[str, list]:
    '''
    This is the handler function that will be called by the serverless worker.
    '''
    print("Job received by handler: {}".format(job))

    # Get job input
    job_input = job['input']

    # Prompts
    if MODEL_NAME.lower() == "llama-2-7b-chat-hf" or MODEL_NAME.lower() == "llama-2-13b-chat-hf":
        template = LLAMA_TEMPLATE
    else:
        template = DEFAULT_TEMPLATE

    # Use the template
    prompt = template(job_input['prompt'])

    # Validate the inputs
    sampling_params = job_input.get('sampling_params', None)
    if sampling_params:
        sampling_params = validate_sampling_params(sampling_params)

        # Sampling parameters
        # https://github.com/vllm-project/vllm/blob/main/vllm/sampling_params.py#L7
        sampling_params = SamplingParams(**sampling_params)
    else:
        sampling_params = SamplingParams()

    # Print the job input
    print(job_input)

    # Print the sampling params
    print(sampling_params)

    # Send request to VLLM
    request_id = random_uuid()
    results_generator = llm.generate(prompt, sampling_params, request_id)

    # Non-streaming case
    final_output = None
    async for request_output in results_generator:
        final_output = request_output

    prompt = final_output.prompt
    text_outputs = [
        output.text for output in final_output.outputs]

    # Metrics for the vLLM serverless worker
    metrics = prepare_metrics()
    metrics['job_input'] = job_input
    metrics['input_tokens'] = [len(final_output.prompt_token_ids)] * len(final_output.outputs)
    metrics['output_tokens'] = [len(output.token_ids) for output in final_output.outputs]

    ret = {
        "outputs": text_outputs,
        "runpod_internal": {
            "metrics": metrics
        }
    }
    return ret


# Start the serverless worker
if STREAMING:
    print("Starting the vLLM serverless worker with streaming enabled.")
    runpod.serverless.start(
        {"handler": handler_streaming, "concurrency_controller": concurrency_controller, "return_aggregate_stream": True})
else:
    print("Starting the vLLM serverless worker with streaming disabled.")
    runpod.serverless.start(
        {"handler": handler, "concurrency_controller": concurrency_controller})
