"""Verify actual Node/Python runtimes can replay each other's persisted sessions."""

import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.context import ContextManager
from agent.runtime import AgentRuntime
from agent.store import SessionStore
from agent.tools import create_tools
from tests.helpers import FakeClient, final, tool

CREATE_NODE = r"""
import { SessionStore } from './src/store.js';
import { AgentRuntime } from './src/runtime.js';
import { ContextManager } from './src/context.js';
import { createTools } from './src/tools.js';
const store = new SessionStore({ dataDir: process.argv[1] });
const session = await store.create('compat', 'Node conversation');
const replies = [
  {choices:[{message:{role:'assistant',content:null,tool_calls:[{id:'c1',type:'function',function:{name:'todo',arguments:JSON.stringify({action:'add',id:null,text:'from-node'})}}]}}]},
  {choices:[{message:{role:'assistant',content:'saved'}}]}
];
const runtime = new AgentRuntime({store, client:{complete:async()=>replies.shift()}, registry:createTools(), context:new ContextManager()});
const result = await runtime.run({userId:'compat',sessionId:session.id,input:'add',requestId:'same'});
if(result.status !== 'ok') throw new Error('Node fixture failed');
console.log(session.id);
"""

READ_NODE = r"""
import { SessionStore } from './src/store.js';
import { AgentRuntime } from './src/runtime.js';
import { ContextManager } from './src/context.js';
import { createTools } from './src/tools.js';
const store = new SessionStore({ dataDir: process.argv[1] });
const runtime = new AgentRuntime({store, client:{complete:async()=>{throw new Error('must not call model');}}, registry:createTools(), context:new ContextManager()});
const result = await runtime.run({userId:'compat',sessionId:process.argv[2],input:'add',requestId:'same'});
const saved = await store.get('compat',process.argv[2]);
if(!result.replayed || result.status !== 'ok' || saved.todos.length !== 1 || saved.todos[0].text !== 'from-python') throw new Error('Python state was not replayed');
console.log('Python -> Node: passed');
"""


async def main():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("This optional cross-language check requires Node.js and Python")
    with tempfile.TemporaryDirectory(prefix="agent-compat-") as directory:
        node_id = subprocess.check_output([node, "--input-type=module", "-e", CREATE_NODE, directory], cwd=ROOT, text=True).strip()
        store = SessionStore(directory)
        client = FakeClient([])
        runtime = AgentRuntime(store=store, client=client, registry=create_tools(), context=ContextManager())
        result = await runtime.run(user_id="compat", session_id=node_id, input="add", request_id="same")
        if not result["replayed"] or result["status"] != "ok" or client.requests:
            raise AssertionError("Node result was not replayed in Python")
        if (await store.get("compat", node_id))["todos"][0]["text"] != "from-node":
            raise AssertionError("Node todo was not preserved")
        print("Node -> Python: passed")
        session = await store.create("compat", "Python conversation")
        runtime.client = FakeClient([tool("todo", {"action": "add", "id": None, "text": "from-python"}), final()])
        result = await runtime.run(user_id="compat", session_id=session["id"], input="add", request_id="same")
        if result["status"] != "ok":
            raise AssertionError("Python fixture failed")
        completed = subprocess.run([node, "--input-type=module", "-e", READ_NODE, directory, session["id"]],
                                   cwd=ROOT, text=True, check=True, capture_output=True)
        print(completed.stdout.strip())


if __name__ == "__main__":
    asyncio.run(main())
