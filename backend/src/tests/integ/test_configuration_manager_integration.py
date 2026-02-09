#!/usr/bin/env python3
"""
Configuration Manager Integration Tests

Tests the centralized configuration management system that replaces
hardcoded values throughout the codebase with proper priority cascade.

This test suite validates:
1. RAG configuration resolution with proper priority
2. LLM configuration resolution
3. User preferences resolution (legitimate settings only)
4. Configuration dictionary generation
5. Priority cascade behavior

Following Shu testing standards for integration testing.
"""

import sys

from shu.core.config import get_config_manager, get_settings_instance


class ConfigurationManagerIntegrationTest:
    """Integration test suite for ConfigurationManager."""

    def __init__(self):
        self.config_manager = get_config_manager()
        self.settings = get_settings_instance()
        self.test_results = []

    def log_result(self, test_name: str, passed: bool, message: str = ""):
        """Log test result."""
        status = "✅ PASS" if passed else "❌ FAIL"
        self.test_results.append((test_name, passed, message))
        print(f"{status}: {test_name}")
        if message:
            print(f"    {message}")

    def test_rag_configuration_priority_cascade(self):
        """Test RAG configuration follows proper priority cascade."""
        test_name = "RAG Configuration Priority Cascade"

        try:
            # Test with no configs - should use global defaults
            threshold = self.config_manager.get_rag_search_threshold()
            expected = self.settings.rag_search_threshold_default
            assert threshold == expected, f"Expected {expected}, got {threshold}"

            # Test with KB config - should override global default
            kb_config = {"search_threshold": 0.8}
            threshold = self.config_manager.get_rag_search_threshold(kb_config=kb_config)
            assert threshold == 0.8, f"Expected 0.8, got {threshold}"

            # Test with model config - should be overridden by KB config
            model_config = {"search_threshold": 0.9}
            threshold = self.config_manager.get_rag_search_threshold(model_config=model_config, kb_config=kb_config)
            assert threshold == 0.8, f"KB config should override model config, got {threshold}"

            # Test user prefs are ignored for RAG settings (per user feedback)
            user_prefs = {"default_search_threshold": 0.5}
            threshold = self.config_manager.get_rag_search_threshold(
                user_prefs=user_prefs, model_config=model_config, kb_config=kb_config
            )
            assert threshold == 0.8, f"User prefs should not override KB config, got {threshold}"

            self.log_result(test_name, True, "Priority cascade working correctly")

        except Exception as e:
            self.log_result(test_name, False, f"Error: {e}")

    def test_llm_configuration_resolution(self):
        """Test LLM configuration resolution."""
        test_name = "LLM Configuration Resolution"

        try:
            # Test with no configs - should use global defaults
            temperature = self.config_manager.get_llm_temperature()
            expected = self.settings.llm_temperature_default
            assert temperature == expected, f"Expected {expected}, got {temperature}"

            # Test with model config - should override global default
            model_config = {"temperature": 0.3}
            temperature = self.config_manager.get_llm_temperature(model_config=model_config)
            assert temperature == 0.3, f"Expected 0.3, got {temperature}"

            # Test max tokens
            max_tokens = self.config_manager.get_llm_max_tokens(model_config={"max_tokens": 2000})
            assert max_tokens == 2000, f"Expected 2000, got {max_tokens}"

            self.log_result(test_name, True, "LLM configuration resolution working")

        except Exception as e:
            self.log_result(test_name, False, f"Error: {e}")

    def test_user_preferences_resolution(self):
        """Test user preferences resolution (legitimate settings only)."""
        test_name = "User Preferences Resolution"

        try:
            # Test memory depth with user preferences
            user_prefs = {"memory_depth": 15}
            depth = self.config_manager.get_user_memory_depth(user_prefs=user_prefs)
            assert depth == 15, f"Expected 15, got {depth}"

            # Test theme preference
            user_prefs = {"theme": "dark"}
            theme = self.config_manager.get_user_theme(user_prefs=user_prefs)
            assert theme == "dark", f"Expected 'dark', got {theme}"

            # Test defaults when no user prefs
            depth = self.config_manager.get_user_memory_depth()
            expected = self.settings.user_memory_depth_default
            assert depth == expected, f"Expected {expected}, got {depth}"

            self.log_result(test_name, True, "User preferences resolution working")

        except Exception as e:
            self.log_result(test_name, False, f"Error: {e}")

    def test_configuration_dictionaries(self):
        """
        Verify generation and key/value resolution of RAG, LLM, and user preferences configuration dictionaries.

        This test builds sample input configs and asserts that:
        - The RAG configuration dictionary contains the expected keys and that
          `search_threshold`, `max_results`, and `context_format` resolve to the provided values.
        - The LLM configuration dictionary contains the keys `temperature`, `max_tokens`, and `timeout`
          and that `temperature` and `max_tokens` resolve to the provided values.
        - The user preferences dictionary contains `memory_depth`, `memory_similarity_threshold`,
          `theme`, `language`, and `timezone`, and that `theme` and `memory_depth` resolve to the provided values.

        Logs success when all assertions pass; logs a failure message if any assertion or step raises an exception.
        """
        test_name = "Configuration Dictionary Generation"

        try:
            # Test RAG config dictionary
            kb_config = {"search_threshold": 0.85, "max_results": 15, "context_format": "simple"}

            rag_dict = self.config_manager.get_rag_config_dict(kb_config=kb_config)

            # Verify all expected keys are present
            expected_keys = {
                "instructional_prompt",
                "include_references",
                "reference_format",
                "context_format",
                "prompt_template",
                "search_threshold",
                "max_results",
                "chunk_overlap_ratio",
                "search_type",
            }
            assert set(rag_dict.keys()) == expected_keys, "Missing keys in RAG config dict"

            # Verify values are resolved correctly
            assert rag_dict["search_threshold"] == 0.85, "Search threshold not resolved"
            assert rag_dict["max_results"] == 15, "Max results not resolved"
            assert rag_dict["context_format"] == "simple", "Context format not resolved"

            # Test LLM config dictionary
            model_config = {"temperature": 0.2, "max_tokens": 1500}
            llm_dict = self.config_manager.get_llm_config_dict(model_config=model_config)

            expected_llm_keys = {"temperature", "max_tokens", "timeout"}
            assert set(llm_dict.keys()) == expected_llm_keys, "Missing keys in LLM config dict"
            assert llm_dict["temperature"] == 0.2, "Temperature not resolved"
            assert llm_dict["max_tokens"] == 1500, "Max tokens not resolved"

            # Test user preferences dictionary
            user_prefs = {"theme": "dark", "memory_depth": 8}
            user_dict = self.config_manager.get_user_preferences_dict(user_prefs=user_prefs)

            expected_user_keys = {
                "memory_depth",
                "memory_similarity_threshold",
                "theme",
                "language",
                "timezone",
            }
            assert set(user_dict.keys()) == expected_user_keys, "Missing keys in user prefs dict"
            assert user_dict["theme"] == "dark", "Theme not resolved"
            assert user_dict["memory_depth"] == 8, "Memory depth not resolved"

            self.log_result(test_name, True, "Configuration dictionaries generated correctly")

        except Exception as e:
            self.log_result(test_name, False, f"Error: {e}")

    def test_global_instance_singleton(self):
        """Test that global configuration manager is a singleton."""
        test_name = "Global Instance Singleton"

        try:
            # Get multiple instances
            manager1 = get_config_manager()
            manager2 = get_config_manager()

            # Should be the same instance
            assert manager1 is manager2, "Configuration manager should be singleton"

            # Should have same settings instance
            assert manager1.settings is manager2.settings, "Settings should be shared"

            self.log_result(test_name, True, "Singleton pattern working correctly")

        except Exception as e:
            self.log_result(test_name, False, f"Error: {e}")

    def run_all_tests(self):
        """Run all configuration manager tests."""
        print("🔧 Configuration Manager Integration Tests")
        print("=" * 50)

        # Run all test methods
        self.test_rag_configuration_priority_cascade()
        self.test_llm_configuration_resolution()
        self.test_user_preferences_resolution()
        self.test_configuration_dictionaries()
        self.test_global_instance_singleton()

        # Summary
        total_tests = len(self.test_results)
        passed_tests = sum(1 for _, passed, _ in self.test_results if passed)

        print("\n" + "=" * 50)
        print(f"📊 Test Results: {passed_tests}/{total_tests} passed")

        if passed_tests == total_tests:
            print("🎉 All tests passed!")
            return True
        print("❌ Some tests failed!")
        for test_name, passed, message in self.test_results:
            if not passed:
                print(f"   FAILED: {test_name} - {message}")
        return False


def main():
    """Run configuration manager integration tests."""
    test_suite = ConfigurationManagerIntegrationTest()
    success = test_suite.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
