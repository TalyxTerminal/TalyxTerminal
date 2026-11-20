import type { ModuleContext } from "@talyx/sdk";
import { jsonRequest, privateRequest } from "../api";
import type { ProjectSummary } from "../documents/service";

export type WorkspaceProject = ProjectSummary;
export interface FileEntry { path: string; uri: string; kind: "file" | "directory" | "missing" | "unavailable" }
export interface HistoryRevision { revisionId: string; createdAt: string; reason: string; size: number }
export interface FileHistory { sourceId: string; path: string; revisions: HistoryRevision[]; warnings: string[] }
/** The TALYX module a project is: its manifest's id and name, and whether it lives in TALYX's modules folder. */
export interface ProjectModule { id: string; name: string; directory: string; installed: boolean }
export { jsonRequest };
const project = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
export async function projectFiles(ctx: ModuleContext, projectId: string, path = "") { return (await privateRequest<{ entries: FileEntry[] }>(ctx, `${project(projectId)}/files?path=${encodeURIComponent(path)}`)).entries; }
export function openInVsCode(ctx: ModuleContext, projectId: string) { return privateRequest<{ opened: string }>(ctx, `${project(projectId)}/vscode`, { method: "POST" }); }
export function debugInVsCode(ctx: ModuleContext, projectId: string) { return privateRequest<{ launch: string }>(ctx, `${project(projectId)}/debug`, { method: "POST" }); }
export async function projectModule(ctx: ModuleContext, projectId: string) { return (await privateRequest<{ module: ProjectModule | null }>(ctx, `${project(projectId)}/module`)).module; }
export async function touchProject(ctx: ModuleContext, projectId: string) {
  return (await privateRequest<{ project: WorkspaceProject }>(ctx, `${project(projectId)}/activate`, jsonRequest({}))).project;
}
export async function removeProject(ctx: ModuleContext, item: WorkspaceProject, mode: "forget" | "trash") {
  return privateRequest(ctx, `${project(item.projectId)}/remove`, jsonRequest({ mode, expectedRoot: item.root, rootIdentity: item.rootIdentity }));
}
export function history(ctx: ModuleContext, projectId: string, path: string) { return privateRequest<FileHistory>(ctx, `${project(projectId)}/history?path=${encodeURIComponent(path)}`); }
export function historyRevision(ctx: ModuleContext, projectId: string, path: string, revisionId: string) { return privateRequest<{ content: string; version: string }>(ctx, `${project(projectId)}/history/${encodeURIComponent(revisionId)}?path=${encodeURIComponent(path)}`); }
