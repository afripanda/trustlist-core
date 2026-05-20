// Copyright 2026 The TrustList Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Jest configuration for the TypeScript event-bus SDK. Unit tests under
// test/unit/ need no broker; integration tests under test/integration/ run a
// real produce -> consume round-trip against RedPanda and skip themselves when
// the TRUSTLIST_EVENT_BUS_* environment variables are unset.

/** @type {import('ts-jest').JestConfigWithTsJest} */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  // Integration tests poll a real broker; give them generous head-room.
  testTimeout: 60000,
};
