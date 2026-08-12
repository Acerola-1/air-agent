/** 用户存储（node-only：fs + bcrypt）.
 *
 *  简易本地账号密码登录：用户存 JSON 文件 `data/users.json`（gitignored），
 *  密码 bcrypt 哈希。单机/小团队场景够用；并发写用「写临时文件 + rename」原子替换。
 */
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import bcrypt from "bcryptjs";

export type StoredUser = {
  id: string;
  username: string;
  passwordHash: string;
  createdAt: number;
};

const USERS_FILE = path.join(process.cwd(), "data", "users.json");

function loadUsers(): Record<string, StoredUser> {
  try {
    return JSON.parse(readFileSync(USERS_FILE, "utf-8")) as Record<string, StoredUser>;
  } catch {
    return {};
  }
}

function saveUsers(users: Record<string, StoredUser>): void {
  mkdirSync(path.dirname(USERS_FILE), { recursive: true });
  const tmp = `${USERS_FILE}.tmp`;
  writeFileSync(tmp, JSON.stringify(users, null, 2), "utf-8");
  renameSync(tmp, USERS_FILE); // 原子替换，避免半写
}

export function findUserByUsername(username: string): StoredUser | undefined {
  const users = loadUsers();
  return Object.values(users).find((u) => u.username === username);
}

export function findUserById(id: string): StoredUser | undefined {
  const users = loadUsers();
  return users[id];
}

/** 创建用户；用户名已存在返回 null */
export function createUser(username: string, password: string): StoredUser | null {
  if (findUserByUsername(username)) return null;
  const user: StoredUser = {
    id: randomUUID(),
    username,
    passwordHash: bcrypt.hashSync(password, 10),
    createdAt: Date.now(),
  };
  const users = loadUsers();
  users[user.id] = user;
  saveUsers(users);
  return user;
}

export function verifyPassword(user: StoredUser, password: string): boolean {
  return bcrypt.compareSync(password, user.passwordHash);
}

/** 校验用户名/密码格式（注册用） */
export function validateCredentials(
  username: string,
  password: string,
): string | null {
  if (!/^[a-zA-Z0-9_\-]{3,32}$/.test(username)) {
    return "用户名需 3-32 位字母/数字/下划线/连字符";
  }
  if (password.length < 6) {
    return "密码至少 6 位";
  }
  return null;
}

// 供测试/调试用的稳定 id（保留，避免误删）
export const userStoreFile = USERS_FILE;
export const _idHash = (s: string) => createHash("sha256").update(s).digest("hex");
