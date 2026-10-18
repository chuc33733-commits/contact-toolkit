# -*- coding: utf-8 -*-
"""
测试运行脚本
"""

import sys
import os
import unittest
import time
from io import StringIO

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("通讯录处理工具 - 测试套件")
    print("=" * 60)
    
    # 发现并运行测试
    test_dir = os.path.dirname(os.path.abspath(__file__))
    loader = unittest.TestLoader()
    suite = loader.discover(test_dir, pattern='test_*.py')
    
    # 创建测试运行器
    stream = StringIO()
    runner = unittest.TextTestRunner(
        stream=stream,
        verbosity=2,
        buffer=True
    )
    
    # 运行测试
    start_time = time.time()
    result = runner.run(suite)
    end_time = time.time()
    
    # 输出结果
    output = stream.getvalue()
    print(output)
    
    # 输出统计信息
    print("\n" + "=" * 60)
    print("测试统计:")
    print(f"运行时间: {end_time - start_time:.2f} 秒")
    print(f"总测试数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print(f"跳过: {len(result.skipped) if hasattr(result, 'skipped') else 0}")
    
    # 输出失败和错误详情
    if result.failures:
        print("\n" + "-" * 60)
        print("失败的测试:")
        for test, traceback in result.failures:
            print(f"\n{test}:")
            print(traceback)
    
    if result.errors:
        print("\n" + "-" * 60)
        print("错误的测试:")
        for test, traceback in result.errors:
            print(f"\n{test}:")
            print(traceback)
    
    print("=" * 60)
    
    # 返回测试是否全部通过
    return len(result.failures) == 0 and len(result.errors) == 0


def run_specific_test(test_module):
    """运行特定测试模块"""
    print(f"运行测试模块: {test_module}")
    print("-" * 40)
    
    try:
        # 导入测试模块
        module = __import__(test_module)
        
        # 创建测试套件
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        
        # 运行测试
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        return len(result.failures) == 0 and len(result.errors) == 0
        
    except ImportError as e:
        print(f"无法导入测试模块 {test_module}: {e}")
        return False


def main():
    """主函数"""
    if len(sys.argv) > 1:
        # 运行特定测试
        test_module = sys.argv[1]
        success = run_specific_test(test_module)
    else:
        # 运行所有测试
        success = run_all_tests()
    
    # 根据测试结果设置退出码
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()