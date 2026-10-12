"""每任务独立 QuickJS worker，外层取消或超时也回收进程."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
from pathlib import Path
from typing import Any

from common.config import config


def _worker(
    connection, raw: str, code: str, memory: int, seconds: float, max_result: int
) -> None:
    # worker 不注册 Python/OS 回调，JS 只能操作注入的 JSON 与固定函数。
    from quickjs_rs import Runtime

    runtime = context = None
    try:
        runtime = Runtime(memory_limit=memory)
        context = runtime.new_context(timeout=seconds)
        context.globals["rawPayload"] = raw
        context.globals["computationCode"] = code
        helpers = Path(__file__).with_name("helpers.js").read_text()
        result = context.eval(
            helpers
            + r"""
          (()=>{
            let data=JSON.parse(rawPayload);
            if(Array.isArray(data)&&data.length===1&&data[0]?.type==="text") data=JSON.parse(data[0].text);
            function freeze(value){if(value&&typeof value==="object"){Object.freeze(value); for(const key of Object.keys(value)) freeze(value[key]);} return value;}
            freeze(data); freeze(pollutionRules);
            for(const value of [Object,Array,Map,Set,Function,JSON,Number,String,Date,Reflect,Math,RegExp,RegExp.prototype,Object.prototype,Array.prototype,Map.prototype,Set.prototype,Function.prototype,Number.prototype,String.prototype,Date.prototype]) Object.freeze(value);
            // 迭代器有独立原型，冻结 Map.prototype 并不能保护覆盖计数的序列化。
            for(const iterator of [new Map().entries(),new Set().values(),[].values(),''[Symbol.iterator]()]) {
              for(let prototype=Object.getPrototypeOf(iterator);prototype;prototype=Object.getPrototypeOf(prototype)) Object.freeze(prototype);
            }
            Object.freeze(globalThis);
            const visits=new Map();
            const tables=new Map();
            function discover(value,path){if(Array.isArray(value)){tables.set(path,value.length);return;}
              if(value && typeof value==="object") for(const key of Object.keys(value)) discover(value[key],path+"/"+key.replace(/~/g,"~0").replace(/\//g,"~1"));}
            discover(data,"");
            function at(path){if(path===""){if(Array.isArray(data))return data;if(tables.size)throw Error("请选择实际记录数组路径，不以根对象代替完整表");return [data];}
              if(!tables.has(path)) throw Error("路径不是完整记录表");
              if(!path.startsWith("/")) throw Error("数据路径应为 JSON pointer");
              let value=data; for(const key of path.slice(1).split("/")) value=value[key.replace(/~1/g,"/").replace(/~0/g,"~")];
              if(!Array.isArray(value)) throw Error("路径不是记录数组"); return value;}
            const helpers=Object.freeze({
              pollutionLevel,
              scan(path,fn){if(visits.has(path)) throw Error("同一范围只能扫描一次，请在回调中完成统计");
                const rows=at(path); let count=0;
                for(let i=0;i<rows.length;i++){const returned=fn(rows[i],i); if(returned && typeof returned.then==="function") throw Error("scan 只支持同步回调"); count++;}
                visits.set(path,{input_records:rows.length,processed_records:count});},
              finite(value){return typeof value==="number"&&Number.isFinite(value);}
            });
            const compute=new Function("data","helpers", '"use strict";\n'+computationCode);
            const output=compute(data,helpers);
            const scope=output && output.scope;
            if(!output||typeof output!=="object"||typeof output.then==="function"||typeof scope!=="string"||!scope.trim()) throw Error("结果必须同步返回 result 和非空 scope 范围说明");
            if(!Object.prototype.hasOwnProperty.call(output,"result")||visits.size===0) throw Error("必须完整 scan 问题范围并返回 result");
            return JSON.stringify({result:output.result,scope,coverage:Array.from(visits,([path,count])=>({path,...count})),helper_version:"v1"}, (key,value)=>{if(value===undefined || typeof value==="function" || typeof value==="symbol" || typeof value==="number"&&!Number.isFinite(value)) throw Error("结果包含不可序列化或非有限数值，须明确缺失值处理"); return value;});
          })()
        """
        )
        if not isinstance(result, str) or len(result.encode()) > max_result:
            raise ValueError("计算结果过大，请在全量扫描后返回更小的聚合结果")
        connection.send({"ok": True, "output": result})
    except BaseException as exc:
        connection.send(
            {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        )
    finally:
        if context is not None:
            context.close()
        if runtime is not None:
            runtime.close()
        connection.close()


async def execute_code(
    raw: str, code: str, *, remaining_seconds: float | None = None
) -> dict[str, Any]:
    """执行并检查结果，进程被取消时不会留在后台运行."""
    if len(raw.encode()) > config.QUICKJS_MAX_INPUT_BYTES or len(code.encode()) > 64000:
        raise ValueError("数据或代码超出执行输入配额，尚未完成计算")
    timeout = min(
        config.QUICKJS_TIMEOUT_SECONDS,
        remaining_seconds or config.DATA_PROCESSING_TIMEOUT_SECONDS,
    )
    if timeout <= 0 or config.QUICKJS_MEMORY_MIB < 1:
        raise ValueError("计算资源配置无效")
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_worker,
        args=(
            child,
            raw,
            code,
            config.QUICKJS_MEMORY_MIB * 1024 * 1024,
            timeout,
            config.QUICKJS_MAX_RESULT_BYTES,
        ),
        daemon=True,
    )
    process.start()
    child.close()
    try:
        # 初始化也在阶段的外层总时限内；JS 引擎另有执行限时。
        async with asyncio.timeout(
            min(
                timeout + 5, remaining_seconds or config.DATA_PROCESSING_TIMEOUT_SECONDS
            )
        ):
            while not parent.poll():
                if not process.is_alive():
                    raise ValueError("计算进程提前结束，尚未完成")
                await asyncio.sleep(0.02)
            response = parent.recv()
        if not response["ok"]:
            raise ValueError(response["error"])
        return json.loads(response["output"])
    finally:
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 2)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 1)
        parent.close()
