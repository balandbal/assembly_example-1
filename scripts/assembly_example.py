#!/usr/bin/env python3

from __future__ import print_function
from six.moves import input

import sys
import copy
import rospy
import moveit_commander
import moveit_msgs.msg
import geometry_msgs.msg
import numpy as np
import tf.transformations as tft
from franka_gripper.msg import GraspActionGoal
from std_srvs.srv import Empty, EmptyRequest

try:
    from math import pi, tau, dist, fabs, cos
except:  # For Python 2 compatibility
    from math import pi, fabs, cos, sqrt

    tau = 2.0 * pi

    def dist(p, q):
        return sqrt(sum((p_i - q_i) ** 2.0 for p_i, q_i in zip(p, q)))


from std_msgs.msg import String
from moveit_commander.conversions import pose_to_list, list_to_pose


def all_close(goal, actual, tolerance):
    """
    Convenience method for testing if the values in two lists are within a tolerance of each other.
    For Pose and PoseStamped inputs, the angle between the two quaternions is compared (the angle
    between the identical orientations q and -q is calculated correctly).
    @param: goal       A list of floats, a Pose or a PoseStamped
    @param: actual     A list of floats, a Pose or a PoseStamped
    @param: tolerance  A float
    @returns: bool
    """
    if type(goal) is list:
        for index in range(len(goal)):
            if abs(actual[index] - goal[index]) > tolerance:
                return False

    elif type(goal) is geometry_msgs.msg.PoseStamped:
        return all_close(goal.pose, actual.pose, tolerance)

    elif type(goal) is geometry_msgs.msg.Pose:
        x0, y0, z0, qx0, qy0, qz0, qw0 = pose_to_list(actual)
        x1, y1, z1, qx1, qy1, qz1, qw1 = pose_to_list(goal)
        # Euclidean distance
        d = dist((x1, y1, z1), (x0, y0, z0))
        # phi = angle between orientations
        cos_phi_half = fabs(qx0 * qx1 + qy0 * qy1 + qz0 * qz1 + qw0 * qw1)
        return d <= tolerance and cos_phi_half >= cos(tolerance / 2.0)

    return True


class AssemblyDemo(object):
    """AssemblyDemo"""

    def __init__(self):
        super(AssemblyDemo, self).__init__()

        # First initialize `moveit_commander`_ and a `rospy`_ node:
        moveit_commander.roscpp_initialize(sys.argv)
        rospy.init_node("assembly_example", anonymous=True)       

        if rospy.has_param('~with_assembly_manager') and rospy.get_param('~with_assembly_manager'):
            # signal assembly manager that we are ready
            rospy.wait_for_service('user_ready')
            user_ready = rospy.ServiceProxy('user_ready', Empty)
            user_ready(EmptyRequest())
        
        # Instantiate a `RobotCommander`_ object. Provides information such as the robot's
        # kinematic model and the robot's current joint states
        self.robot = robot = moveit_commander.RobotCommander()

        # Instantiate a `PlanningSceneInterface`_ object.  This provides a remote interface
        # for getting, setting, and updating the robot's internal understanding of the
        # surrounding world:
        scene = moveit_commander.PlanningSceneInterface()

        table_pose = geometry_msgs.msg.PoseStamped()
        table_pose.pose.orientation.w = 1
        table_pose.pose.position.x = 0.5
        table_pose.pose.position.y = 0.0
        table_pose.pose.position.z = 0.20
        table_pose.header.frame_id = 'world'

        while not self.wait_for_state_update(scene, 'table', True, False, 4):
            scene.add_box('table', table_pose, size=(0.50, 0.80, 0.4))

        print('Added/Found table to/in planning scene')
        # Instantiate a `MoveGroupCommander`_ object.  This object is an interface
        # to a planning group (group of joints).  In this tutorial the group is the primary
        # arm joints in the Panda robot, so we set the group's name to "panda_arm".
        # If you are using a different robot, change this value to the name of your robot
        # arm planning group.
        # This interface can be used to plan and execute motions:
        group_name = "panda_arm"
        move_group = moveit_commander.MoveGroupCommander(group_name)
        move_group.set_support_surface_name('table')

        self.move_group_hand = moveit_commander.MoveGroupCommander(
            'panda_hand')

        # Create a `DisplayTrajectory`_ ROS publisher which is used to display
        # trajectories in Rviz:
        display_trajectory_publisher = rospy.Publisher(
            "/move_group/display_planned_path",
            moveit_msgs.msg.DisplayTrajectory,
            queue_size=20,
        )

        # We can get the name of the reference frame for this robot:
        planning_frame = move_group.get_planning_frame()
        print("============ Planning frame: %s" % planning_frame)

        # We can also print the name of the end-effector link for this group:
        eef_link = move_group.get_end_effector_link()
        print("============ End effector link: %s" % eef_link)

        # We can get a list of all the groups in the robot:
        group_names = robot.get_group_names()
        print("============ Available Planning Groups:", robot.get_group_names())

        # Sometimes for debugging it is useful to print the entire state of the
        # robot:
        print("============ Printing robot state")
        print(robot.get_current_state())
        print("")
        # END_SUB_TUTORIAL

        self.grasp_action_pub = rospy.Publisher(
            '/franka_gripper/grasp/goal', GraspActionGoal, latch=True, queue_size=20)

        # Misc variables
        self.box_name = ""
        self.robot = robot
        self.scene = scene
        self.move_group = move_group
        self.display_trajectory_publisher = display_trajectory_publisher
        self.planning_frame = planning_frame
        self.eef_link = eef_link
        self.group_names = group_names

    def time_parameterization(self, plan, velocity_scaling=0.1, acceleration_scaling=0.1, algorithm="time_optimal_trajectory_generation"):
        ref_state = self.robot.get_current_state()
        retimed_plan = self.move_group.retime_trajectory(
            ref_state,
            plan,
            velocity_scaling_factor=velocity_scaling,
            acceleration_scaling_factor=acceleration_scaling,
            algorithm=algorithm
        )

        return retimed_plan

    def open_gripper(self):

        joint_goal = self.move_group_hand.get_current_joint_values()
        # print(joint_goal)
        joint_goal[0] = 0.04
        joint_goal[1] = 0.04

        self.move_group_hand.go(joint_goal, wait=True)

        # Calling ``stop()`` ensures that there is no residual movement
        self.move_group_hand.stop()

        # For testing:
        current_joints = self.move_group_hand.get_current_joint_values()
        return all_close(joint_goal, current_joints, 0.01)

    def close_gripper(self):
        gag = GraspActionGoal()
        gag.goal.width = 0.03
        gag.goal.epsilon.inner = 0.01
        gag.goal.epsilon.outer = 0.01
        gag.goal.force = 10
        gag.goal.speed = 0.05
        self.grasp_action_pub.publish(gag)

    def screw(self):
        move_group = self.move_group
        move_group.set_max_velocity_scaling_factor(1)
        move_group.set_max_acceleration_scaling_factor(1)

        self.open_gripper()
        joint_goal = move_group.get_current_joint_values()
        joint_goal[6] = -7. * np.pi / 8.
        move_group.go(joint_goal, wait=True)
        move_group.stop()

        screw_pos_msg = rospy.wait_for_message(
            '/screw_pos', geometry_msgs.msg.PointStamped)
        z0 = screw_pos_msg.point.z
        z1 = z0
        z2 = z0

        print(z0, z1, z2)

        while z0 - z2 < 0.006:

            joint_goal = move_group.get_current_joint_values()
            if joint_goal[6] >= np.pi / 2:
                self.open_gripper()
                joint_goal[6] = -7. * np.pi / 8.
                move_group.go(joint_goal, wait=True)
                move_group.stop()

            else:
                self.close_gripper()
                rospy.sleep(1)
                joint_goal[6] = 7. * np.pi / 8.  # += np.pi / 4.
                move_group.go(joint_goal, wait=True)
                move_group.stop()

            screw_pos_msg = rospy.wait_for_message(
                '/screw_pos', geometry_msgs.msg.PointStamped)
            z2 = screw_pos_msg.point.z

    def plan_cartesian_path(self, waypoints, velocity_scaling=0.4, acceleration_scaling=0.4, reparam_algo="time_optimal_trajectory_generation"):
        # Copy class variables to local variables to make the web tutorials more clear.
        # In practice, you should use the class variables directly unless you have a good
        # reason not to.
        move_group = self.move_group

        (plan, fraction) = move_group.compute_cartesian_path(
            waypoints, 0.01, 0  # waypoints to follow  # eef_step
        )  # jump_threshold

        plan = self.time_parameterization(
            plan, velocity_scaling, acceleration_scaling, reparam_algo)

        # Note: We are just planning, not asking move_group to actually move the robot yet:
        return plan, fraction

    def display_trajectory(self, plan):
        robot = self.robot
        display_trajectory_publisher = self.display_trajectory_publisher

        display_trajectory = moveit_msgs.msg.DisplayTrajectory()
        display_trajectory.trajectory_start = robot.get_current_state()
        display_trajectory.trajectory.append(plan)
        # Publish
        display_trajectory_publisher.publish(display_trajectory)

    def execute_plan(self, plan):
        move_group = self.move_group
        move_group.execute(plan, wait=True)

    def wait_for_state_update(
        self, scene, box_name, box_is_known=False, box_is_attached=False, timeout=4
    ):
        start = rospy.get_time()
        seconds = rospy.get_time()
        while (seconds - start < timeout) and not rospy.is_shutdown():
            # Test if the box is in attached objects
            attached_objects = scene.get_attached_objects([box_name])
            is_attached = len(attached_objects.keys()) > 0

            # Test if the box is in the scene.
            # Note that attaching the box will remove it from known_objects
            is_known = box_name in scene.get_known_object_names()

            # Test if we are in the expected state
            if (box_is_attached == is_attached) and (box_is_known == is_known):
                return True

            # Sleep so that we give other threads time on the processor
            rospy.sleep(0.1)
            seconds = rospy.get_time()

        # If we exited the while loop without returning then we timed out
        return False


def main():
    try:
        assemblyDemo = AssemblyDemo()

        nonstop = False
        if (len(sys.argv) > 1 and sys.argv[1] == 'nonstop') or rospy.get_param('~nonstop'):
            nonstop = True

        assemblyDemo.move_group.set_named_target('ready')
        assemblyDemo.move_group.go(wait=True)
        assemblyDemo.move_group.stop()
        assemblyDemo.move_group.clear_pose_targets()

        if not nonstop:
            input("============ Press `Enter` to open gripper ...")
        assemblyDemo.open_gripper()

        if not nonstop:
            input("============ Press `Enter` to go to grasp pose ...")
        waypoints = []

        nut_pos_msg = rospy.wait_for_message(
            '/nut_head_pos', geometry_msgs.msg.PointStamped)
        nut_quat_msg = rospy.wait_for_message(
            '/nut_quat', geometry_msgs.msg.QuaternionStamped)

        pose_goal = geometry_msgs.msg.Pose()

        q = np.array([nut_quat_msg.quaternion.x, nut_quat_msg.quaternion.y,
                     nut_quat_msg.quaternion.z, nut_quat_msg.quaternion.w])
        z_axis = tft.quaternion_matrix(q)[:-1, 2]

        if np.isclose(z_axis[2], 1):
            y_axis = np.array([0, 0, 1])
            z_axis = np.array([1, 0, 0])
            x_axis = np.array([0, 1, 0])
            q2 = tft.quaternion_from_euler(np.pi, 0, -tau / 8)
            pos_offset_fixture_x = -0.0
            pos_offset_fixture_z = 0.07
            q_fixture = tft.quaternion_from_euler(np.pi, 0, tau / 8)
        else:
            x_axis = np.cross(z_axis, np.array([0, 0, 1]))
            x_axis /= np.linalg.norm(x_axis)
            y_axis = np.cross(z_axis, x_axis)
            y_axis /= np.linalg.norm(y_axis)
            q2 = tft.quaternion_from_euler(0, 0, - np.pi / 4.)
            pos_offset_fixture_x = -0.1027
            pos_offset_fixture_z = 0
            q_fixture = tft.quaternion_from_euler(-tau / 4, -tau / 8, -tau / 4)

        mat = np.array([[*z_axis, 0.0], [*x_axis, 0.0],
                       [*y_axis, 0.0], [0.0, 0.0, 0.0, 1.0]]).T
        q = tft.quaternion_from_matrix(mat)
        q = tft.quaternion_multiply(q2, q)

        pose_goal.orientation = geometry_msgs.msg.Quaternion(*q)

        pose_goal.position = nut_pos_msg.point
        pose_goal.position.z += 0.155 - (0.0021749 + 0.0058 + 0.001)

        waypoints.append(copy.deepcopy(pose_goal))
        pose_goal.position.z -= 0.05

        waypoints.append(copy.deepcopy(pose_goal))
        plan, fraction = assemblyDemo.plan_cartesian_path(waypoints)
        assemblyDemo.display_trajectory(plan)

        if not nonstop:
            input("============ Press `Enter` to execute grasp ...")
        assemblyDemo.execute_plan(plan)
        if not nonstop:
            input("============ Press `Enter` to close gripper ...")
        assemblyDemo.close_gripper()
        rospy.sleep(2)

        if not nonstop:
            input("============ Press `Enter` to go to fixture ...")
        waypoints.clear()

        fixture_pos_msg = rospy.wait_for_message(
            '/fixture_pos', geometry_msgs.msg.PointStamped)

        pose_goal.orientation = geometry_msgs.msg.Quaternion(*q_fixture)
        pose_goal.position.x = fixture_pos_msg.point.x
        pose_goal.position.y = fixture_pos_msg.point.y
        pose_goal.position.z = 0.57
        pose_goal.position.x += pos_offset_fixture_x
        pose_goal.position.z += pos_offset_fixture_z

        waypoints.append(copy.deepcopy(pose_goal))
        pose_goal.position.z -= 0.07

        waypoints.append(copy.deepcopy(pose_goal))
        plan, fraction = assemblyDemo.plan_cartesian_path(waypoints)
        assemblyDemo.display_trajectory(plan)

        if not nonstop:
            input("============ Press `Enter` to execute grasp ...")
        assemblyDemo.execute_plan(plan)
        assemblyDemo.open_gripper()

        if not nonstop:
            input("============ Press `Enter` to go to grasp pose ...")
        waypoints = []

        screw_pos_msg = rospy.wait_for_message(
            '/screw_head_pos', geometry_msgs.msg.PointStamped)

        pose_goal = geometry_msgs.msg.Pose()
        q = tft.quaternion_from_euler(np.pi, 0, - np.pi / 4.)
        pose_goal.orientation = geometry_msgs.msg.Quaternion(*q)

        pose_goal.position.x = screw_pos_msg.point.x
        pose_goal.position.y = screw_pos_msg.point.y
        pose_goal.position.z = screw_pos_msg.point.z + 0.15

        waypoints.append(copy.deepcopy(pose_goal))
        pose_goal.position.z -= 0.05

        waypoints.append(copy.deepcopy(pose_goal))
        plan, fraction = assemblyDemo.plan_cartesian_path(waypoints)
        assemblyDemo.display_trajectory(plan)

        assemblyDemo.execute_plan(plan)
        if not nonstop:
            input("============ Press `Enter` to close gripper ...")
        assemblyDemo.close_gripper()
        rospy.sleep(2)

        if not nonstop:
            input("============ Press `Enter` to go to screwing pose ...")
        waypoints.clear()
        nut_pos_msg = rospy.wait_for_message(
            '/nut_head_pos', geometry_msgs.msg.PointStamped)
        pose_goal.orientation = geometry_msgs.msg.Quaternion(*q)
        pose_goal.position.x = nut_pos_msg.point.x
        pose_goal.position.y = nut_pos_msg.point.y
        pose_goal.position.z = nut_pos_msg.point.z + 0.03275 + 0.165 - (0.0021749 + 0.0058 + 0.001)

        waypoints.append(copy.deepcopy(pose_goal))
        pose_goal.position.z -= 0.074

        waypoints.append(copy.deepcopy(pose_goal))
        plan, fraction = assemblyDemo.plan_cartesian_path(waypoints)
        assemblyDemo.display_trajectory(plan)

        assemblyDemo.execute_plan(plan)
        # assemblyDemo.open_gripper()
        # pose_goal.position.z -= 0.03
        # waypoints.clear()
        # waypoints.append(copy.deepcopy(pose_goal))
        # plan, fraction = assemblyDemo.plan_cartesian_path(waypoints)
        # assemblyDemo.display_trajectory(plan)
        # assemblyDemo.execute_plan(plan)

        if not nonstop:
            input("============ Press `Enter` to screw ...")

        assemblyDemo.screw()

        if not nonstop:
            input("============ Press `Enter` to lift ...")
        waypoints.clear()
        pose_goal.position.z += 0.20
        waypoints.append(copy.deepcopy(pose_goal))
        plan, fraction = assemblyDemo.plan_cartesian_path(waypoints)
        assemblyDemo.display_trajectory(plan)

        assemblyDemo.execute_plan(plan)
        # assemblyDemo.move_group.set_named_target('ready')
        # assemblyDemo.move_group.go(wait=True)
        # assemblyDemo.move_group.stop()
        # assemblyDemo.move_group.clear_pose_targets()

    except rospy.ROSInterruptException:
        return
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
