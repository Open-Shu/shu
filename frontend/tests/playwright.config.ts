import path from "path";
import { Pool } from "pg";
import { randomUUID } from "crypto";

const baseUrl = process.env.BASE_URL ?? "http://localhost:3000/";
const rawUrl =
  process.env.SHU_DATABASE_URL ??
  "postgresql+asyncpg://shu:password@localhost:5432/shu";
const connectionString = rawUrl.replace(
  "postgresql+asyncpg://",
  "postgresql://",
);

export const buildURL = (uri: string) => {
  return path.join(baseUrl, uri);
};

export enum UserType {
  Password = "password",
  Google = "google",
}

class DB {
  db: Pool;

  constructor() {
    this.db = new Pool({ connectionString });
  }

  async removeUser(email: string): Promise<any> {
    return await this.db.query("DELETE FROM users WHERE email = $1", [email]);
  }

  async activateUser(email: string): Promise<any> {
    return await this.db.query(
      "UPDATE users SET is_active=true WHERE email =  $1",
      [email],
    );
  }

  async createUser(email: string, type: UserType): Promise<any> {
    return await this.db.query(
      `
            INSERT INTO "public"."users" (
                "id", "email", "name", "role", "google_id", "picture_url", "is_active", "created_at", "updated_at",
                "last_login", "password_hash", "auth_method"
            ) VALUES (
                $1, $2, $3, 'regular_user', $4, $5, 't', '2025-10-14 23:15:10.59094',
                '2025-10-15 18:55:58.417414', '2025-10-15 18:55:58.411629', $6, $7);
        `,
      [
        randomUUID(),
        email,
        email,
        type === UserType.Google ? "1234" : null,
        null,
        type === UserType.Password
          ? "$2y$12$OdeaCTGxc3lF7RfGUznJvegcMmnneoQn/RJQmH0Lef3QPFyLv3lsW"
          : null,
        type,
      ],
    );
  }

  async getRandomModel(): Promise<string> {
    // TODO: We are not checking if the provider has streaming activate too.
    const models = await this.db.query(
      `
            SELECT m.name FROM model_configurations m
                LEFT JOIN model_configuration_knowledge_bases mk ON (m.id = mk.model_configuration_id)
                WHERE functionalities::text LIKE '%"supports_streaming": true%' and mk.knowledge_base_id is null
        `,
      [],
    );
    const randomIndex = Math.floor(Math.random() * models.rows.length);
    return models.rows[randomIndex].name;
  }

  async close(): Promise<any> {
    return this.db.end();
  }
}

export const db = new DB();
