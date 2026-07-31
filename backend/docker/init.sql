-- 컨테이너 최초 기동 시 1회만 실행됩니다 (볼륨이 비어 있을 때).
-- 테이블은 SQLAlchemy 가 만들므로 여기서는 데이터베이스 수준 설정만 둡니다.

ALTER DATABASE dukgotgan
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 테스트용 데이터베이스. 운영 데이터와 섞이지 않게 분리합니다.
CREATE DATABASE IF NOT EXISTS dukgotgan_test
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON dukgotgan_test.* TO 'dukgotgan'@'%';
FLUSH PRIVILEGES;
