---
name: Developer files
description: Projects and shared source documents. Open a project or file, read it, edit it at the version you read, save it.
---

Every editor and caller of a file shares one buffer for it: edits change the buffer, saving writes it to disk.

Usual calls
- Open a folder: developer.projects.open {directory} -> its file tree. How to work in the project: developer.projects.instructions.
- Open a file: developer.documents.open {source:{kind:"file", uri:"file:///D:/work/app.py"}} -> documentId and version; no project needed.
- Read: developer.documents.read {documentId} gives whole lines from line 1 with totalLines and nextCursor; a part of a long file by startLine and lineCount. Lines count from the page's startLine.
- Change a few lines: developer.documents.edit {documentId, expectedVersion, edits:[{startLine, endLine, text}]}, lines as numbered in the version you read; to replace the whole text, content instead of edits. Then developer.documents.save.
- documents.list {} finds unsaved files and module documents. If an edit version is stale, read again. For a disk conflict, documents.read_versions compares the buffer, baseline and disk. Pass its version and sourceVersion to documents.resolve as expectedVersion and expectedSourceVersion. Choose external or mine; save after mine.
- documents.read_checkpoint lists captured sources; pass a sourceKey for its text. projects.file_history lists a file's revisions or reads a revisionId; projects.restore_revision restores at the current disk version.
- developer.help lists SDK and extension references. projects.module_status reads the project manifest and runtime status. projects.remove takes the rootUri (as expectedRootUri) and rootIdentity from projects.list; forget keeps the folder, trash moves it to the Recycle Bin.
- Show a file to the user: platform.panels.open_target {module:"developer", panelType:"main", objectId:"file:" + its file URI}; "folder:" + a path opens a folder. platform.workspace.folder {workspaceId} gives the workspace folder.
- For a new TALYX module, read the module doc. After projects.create_module, select its project in Developer and click Use in TALYX to install and start it for the first time. After saving later code changes, click Reload. Enable starts a previously disabled module. To perform the first installation for the user, call platform.modules.install with the returned directory as path.

A save that reports nothing to write means the file already had that text. Use a checkpoint when a research run needs a fixed snapshot of the source.
