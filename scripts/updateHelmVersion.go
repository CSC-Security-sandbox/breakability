package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"os/exec"
	"strings"
)

type UpdateConfig struct {
	FilePath string   `json:"filePath"`
	YamlPath []string `json:"yamlPath"`
}

var digestYAMLPathToKey = map[string]string{
	".images.core.digest":              "core_digest_gcp",
	".images.vcpDbMigrate.digest":      "vcp_db_migrate_digest_gcp",
	".images.googleProxy.digest":       "google_proxy_digest_gcp",
	".images.vcpWorker.digest":         "vcp_worker_digest_gcp",
	".versionsSupported[].sha":         "vcp_worker_digest_gcp",
	".images.telemetryDeployer.digest": "telemetry_deployer_digest_gcp",
	".images.telemetry.digest":         "telemetry_digest_gcp",
	".images.ontapProxy.digest":        "ontap_proxy_digest_gcp",
}

func readDigestsFromFile(filePath string) (map[string]string, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return nil, fmt.Errorf("error opening file: %v", err)
	}
	defer file.Close()

	digests := make(map[string]string)
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			digests[parts[0]] = parts[1]
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("error reading file: %v", err)
	}

	return digests, nil
}

func UpdateKeys(file string, yamlPaths []string, version string, digests map[string]string) error {
	if _, err := os.Stat(file); os.IsNotExist(err) {
		return fmt.Errorf("file %s not found", file)
	}

	log.Printf("Updating %s...\n", file)
	for _, yamlPath := range yamlPaths {
		if !strings.HasPrefix(yamlPath, ".") {
			yamlPath = "." + yamlPath
		}

		if strings.Contains(yamlPath, "[]") {
			pathSegments := strings.Split(yamlPath, "[]")
			if len(pathSegments) < 2 {
				return fmt.Errorf("malformed yamlPath: %s, expected a key after '[]'", yamlPath)
			}
			basePath := pathSegments[0]
			key := pathSegments[1]
			value := version
			if strings.HasSuffix(yamlPath, "sha") {
				digestKey := digestYAMLPathToKey[yamlPath]
				digestValue, exists := digests[digestKey]
				if !exists {
					return fmt.Errorf("digest key %s not found in file", digestKey)
				}
				// Strip the "sha256:" prefix if it exists. We only want the hash value for the sha field.
				if strings.HasPrefix(digestValue, "sha256:") {
					digestValue = strings.TrimPrefix(digestValue, "sha256:")
				}

				value = digestValue
			}

			cmd := exec.Command("yq", "eval", "-i", fmt.Sprintf(`with(%s[]; %s = "%s")`, basePath, key, value), file)
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("failed to update array path %s in file %s: %v\nstderr: %s", yamlPath, file, err, stderr.String())
			}
		} else if strings.HasSuffix(yamlPath, "digest") {
			digestKey := digestYAMLPathToKey[yamlPath]
			digestValue, exists := digests[digestKey]
			if !exists {
				return fmt.Errorf("digest key %s not found in file", digestKey)
			}
			cmd := exec.Command("yq", "eval", "-i", fmt.Sprintf(`%s = "%s"`, yamlPath, digestValue), file)
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("failed to update digest key %s in file %s: %v\nstderr: %s", yamlPath, file, err, stderr.String())
			}
		} else {
			cmd := exec.Command("yq", "eval", "-i", fmt.Sprintf(`%s = "%s"`, yamlPath, version), file)
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("failed to update key %s in file %s: %v\nstderr: %s", yamlPath, file, err, stderr.String())
			}
		}
	}
	return nil
}

func main() {
	if len(os.Args) != 4 {
		log.Println("Usage: ./updateHelmVersion <version> <config.json> <tempFile>")
		os.Exit(1)
	}

	version := strings.TrimPrefix(os.Args[1], "v")
	configFile := os.Args[2]
	tempFile := os.Args[3]

	digests, err := readDigestsFromFile(tempFile)
	if err != nil {
		log.Printf("Error reading digests from file: %v\n", err)
		os.Exit(1)
	}

	data, err := ioutil.ReadFile(configFile)
	if err != nil {
		log.Printf("Error reading config file: %v\n", err)
		os.Exit(1)
	}

	var configs []UpdateConfig
	if err := json.Unmarshal(data, &configs); err != nil {
		log.Printf("Error parsing config file: %v\n", err)
		os.Exit(1)
	}

	for _, config := range configs {
		if err := UpdateKeys(config.FilePath, config.YamlPath, version, digests); err != nil {
			log.Printf("Error updating keys in file %s: %v\n", config.FilePath, err)
			os.Exit(1)
		}
	}

	log.Println("Helm Version update completed!")
}
