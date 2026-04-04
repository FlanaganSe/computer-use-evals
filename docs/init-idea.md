The initial idea is that we need:

1. An implementtaion that can "test" harness evaluations for agent performance with desktop tasks.
2. Able to use codex subscriptions via OAuth

This could almost be considered "harness engineering" if a focus is put on determining optimal ways to cause agents to be able to correctly behave in a desktop based on the intentions of a user. 
An example is that a user may want to "click button in pdf" and that we need to be able to evaluate what impacts the agents ability to click the correct button. 

Right now we just want some scripts and flows that can run evaluation tests. 
They should be simple; understandable; and flexible for change. Ideally they would be across operating systems, but at the very least want to be targetted for macOS for right now. 




_____

# Long term vision

To be able to have some kind of application that allows a user to "record" their screen. Then they may go through some regular user flows (open pdfs, save them, open an excel, reformat the rows, save, etc). We then want to "save" this agent prompt, with correct intentions, and be able to allow an "agent" to go through the flows again on their computer. 

This requires being able to near-always correctly catalogue intent. This is difficult in a desktop because taking multiple screenshots over multiple tasks can be lossy and degrade performance. 

We may want to combine multiple approaches in a way that:
1. Maximizes accuracy
2. Minimizes token use 
3. Maximizes understanding of what the agent is about to do. It needs to get the intentions near-correct (even humans may fail here, so it's up in the air)